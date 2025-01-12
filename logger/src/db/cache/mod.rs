use crate::prelude::*;
use crate::proto::{
    auto_config::*,
    guild_configs::*,
    cache::*,
};
use crate::error::Result;
use byteorder::{BigEndian, ByteOrder};
use redis::{self, RedisWrite, ToRedisArgs, FromRedisValue, aio::ConnectionLike};
use num_derive::FromPrimitive;
use num_traits::FromPrimitive;
use std::io::prelude::*;
use twilight_model::id::*;

/// The single byte compression mode header for values stored in Redis.
#[repr(u8)]
#[derive(FromPrimitive)]
enum CompressionMode {
    /// Uncompressed. The value is entirely uncompressed and can be used as is.
    Uncompressed = 0,
    /// Compressed with zlib. Default compression level: 6.
    Zlib = 1,
}

/// The single byte key prefix for all keys stored in Redis.
#[repr(u8)]
#[derive(Copy, Clone)]
pub(super) enum CachePrefix {
    /// Protobuf configs for per server configuration. Stored in the form of hashes with individual
    /// configs as hash values, keyed by the corresponding CachedGuildConfig subkey.
    GuildConfigs = 1_u8,
    /// Redis sets of per-server user IDs of online users.
    OnlineStatus = 2_u8,
    /// Messages cached.
    Messages = 3_u8,
}

/// A prefixed key schema for 64-bit integer keys. Implements ToRedisArgs, so its generically
/// usable as an argument to direct Redis calls.
#[derive(Copy, Clone)]
pub(super) struct CacheKey<T>(CachePrefix, T);

impl ToRedisArgs for CacheKey<u64> {
    fn write_redis_args<W: ?Sized>(&self, out: &mut W)
    where
        W: RedisWrite,
    {
        let mut key_enc = [self.0 as u8; 9];
        BigEndian::write_u64(&mut key_enc[1..9], self.1);
        out.write_arg(&key_enc[..]);
    }
}

impl ToRedisArgs for CacheKey<(u64, u64)>{
    fn write_redis_args<W: ?Sized>(&self, out: &mut W)
    where
        W: RedisWrite,
    {
        let mut key_enc = [self.0 as u8; 17];
        BigEndian::write_u64(&mut key_enc[1..9], self.1.0);
        BigEndian::write_u64(&mut key_enc[9..17], self.1.1);
        out.write_arg(&key_enc[..]);
    }
}

#[derive(Copy, Clone)]
pub(super) struct Id<T>(T);

impl ToRedisArgs for Id<u64> {

    fn write_redis_args<W: ?Sized>(&self, out: &mut W)
    where
        W: RedisWrite,
    {
        let mut key_enc = [0; 8];
        BigEndian::write_u64(&mut key_enc[0..8], self.0);
        out.write_arg(&key_enc[..]);
    }

}

pub struct OnlineStatus {
    pipeline: redis::Pipeline
}

impl Default for OnlineStatus {
    fn default() -> Self {
        Self { pipeline: redis::pipe().atomic().clone() }
    }
}

impl OnlineStatus {

    pub fn new() -> Self {
        Self::default()
    }

    pub fn set_online(&mut self, guild_id: GuildId, online: impl IntoIterator<Item=UserId>)
                      -> &mut Self {
        let key = CacheKey(CachePrefix::OnlineStatus, guild_id.0);
        let ids: Vec<Id<u64>> = online.into_iter().map(|id| Id(id.0)).collect();
        self.pipeline
            .del(key).ignore()
            .sadd(key, ids).ignore()
            .expire(key, 3600);
        self
    }

    pub fn build(self) -> redis::Pipeline {
        self.pipeline
    }

}

pub struct CachedMessage {
    proto: Protobuf<CachedMessageProto>,
}

impl CachedMessage {

    pub fn new(message: twilight_model::channel::Message) -> Self {
        let mut msg = CachedMessageProto::new();
        msg.set_id(message.id.0);
        msg.set_channel_id(message.channel_id.0);
        msg.set_content(message.content);
        if let Some(guild_id) = message.guild_id {
            msg.set_guild_id(guild_id.0)
        }

        let user = msg.mut_author();
        let author = &message.author;
        user.set_id(author.id.0);
        user.set_username(author.name.clone());
        user.set_discriminator(message.author.discriminator() as u32);

        Self {
            proto: Protobuf(msg)
        }
    }

    pub fn flush(self) -> redis::Pipeline {
        let channel_id = self.proto.0.get_channel_id();
        let id = self.proto.0.get_id();
        let key = CacheKey(CachePrefix::Messages, (channel_id, id));
        let mut pipeline = redis::pipe();
        pipeline.atomic().set(key, self.proto).expire(key, 3600);
        pipeline
    }

    pub fn delete(channel_id: ChannelId, id: MessageId) -> redis::Cmd {
        Self::bulk_delete(channel_id, vec![id])
    }

    pub fn bulk_delete(
        channel_id: ChannelId,
        ids: impl IntoIterator<Item=MessageId>
    ) -> redis::Cmd {
        let keys: Vec<CacheKey<(u64, u64)>> =
            ids.into_iter()
               .map(|id| CacheKey(CachePrefix::Messages, (channel_id.0, id.0)))
               .collect();
        redis::Cmd::del(keys)
    }

}

pub struct Protobuf<T: protobuf::Message>(T);

impl<T: protobuf::Message> Protobuf<T> {

    fn parse_protobuf(data: impl AsRef<[u8]>) -> redis::RedisResult<Self> {
        match T::parse_from_bytes(data.as_ref()) {
            Ok(proto) => Ok(Self::from(proto)),
            Err(err) => Err(Self::convert_error(err))
        }
    }

    fn convert_error(err: protobuf::error::ProtobufError) -> redis::RedisError {
        use protobuf::error::ProtobufError;
        use redis::{RedisError, ErrorKind};
        match err {
            ProtobufError::IoError(io_err) => RedisError::from(io_err),
            general_err => RedisError::from(
                (ErrorKind::ResponseError, "Failed to parse Protobuf", general_err.to_string())
            )
        }
    }

}

impl<T: protobuf::Message> From<T> for Protobuf<T> {
    fn from(value: T) -> Self {
        Self(value)
    }
}

impl<T: protobuf::Message> ToRedisArgs for Protobuf<T> {

    fn write_redis_args<W: ?Sized>(&self, out: &mut W) where W: RedisWrite {
        out.write_arg(
            self.0
                .write_to_bytes()
                .expect("Should not be generating malformed Protobufs.")
                .as_slice());
    }

}

impl<T: protobuf::Message> FromRedisValue for Protobuf<T> {

    fn from_redis_value(value: &redis::Value) -> redis::RedisResult<Self> {
        use redis::{RedisError, Value, ErrorKind};
        match value {
            Value::Data(data) => Self::parse_protobuf(data),
            val =>  Err(RedisError::from(
                    (ErrorKind::ResponseError, "Type incompatible with Protobufs",
                     format!("Invalid input: {:?}", val)))),
        }
    }

}

fn compress_payload(payload: &[u8]) -> Result<Vec<u8>> {
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::new(6));
    encoder.write_all(&payload)?;
    let mut output = encoder.finish()?;
    let compression_mode = if output.len() < payload.len() {
        CompressionMode::Zlib
    } else {
        output = payload.to_vec();
        CompressionMode::Uncompressed
    };
    output.insert(0, compression_mode as u8);
    Ok(output)
}

fn decompress_payload(payload: &[u8]) -> Result<Vec<u8>> {
    if payload.len() < 1 {
        return Ok(payload.to_vec());
    }
    let data = &payload[1..];
    match CompressionMode::from_u8(payload[0]) {
        Some(CompressionMode::Uncompressed) => Ok(data.to_vec()),
        Some(CompressionMode::Zlib) => {
            let mut output: Vec<u8> = Vec::new();
            flate2::read::ZlibDecoder::new(data).read_to_end(&mut output)?;
            Ok(output)
        }
        // Default to returning the original payload if no match for the header is found
        None => Ok(payload.to_vec()),
    }
}

#[async_trait]
pub trait Cacheable: Sized {
    type Key;
    async fn get<I, C>(connection: &mut C, key: I) -> Result<Option<Self>>
    where
        I: Into<Self::Key> + Send,
        C: ConnectionLike + Send;
    async fn set<I, C>(connection: &mut C, key: I, value: &Self) -> Result<()>
    where
        I: Into<Self::Key> + Send,
        C: ConnectionLike + Send;
}

#[async_trait]
impl<T: protobuf::Message + CachedGuildConfig + Send> Cacheable for T {
    type Key = GuildId;

    async fn get<I, C>(connection: &mut C, key: I) -> Result<Option<Self>>
    where
        I: Into<GuildId> + Send,
        C: ConnectionLike + Send,
    {
        let key = CacheKey(CachePrefix::GuildConfigs, key.into().0);
        let response: Option<Vec<u8>> = redis::Cmd::hget(key, Self::SUBKEY)
            .query_async(connection)
            .await?;
        let proto = if let Some(payload) = response {
            let decomp = decompress_payload(&payload[..])?;
            Self::parse_from_bytes(&decomp[..])?
        } else {
            // If nothing has been found, return the default value for the type.
            Self::new()
        };
        return Ok(Some(proto));
    }

    async fn set<I, C>(connection: &mut C, key: I, value: &Self) -> Result<()>
    where
        I: Into<GuildId> + Send,
        C: ConnectionLike + Send,
    {
        let mut proto_enc: Vec<u8> = Vec::new();
        value.write_to_vec(&mut proto_enc)?;
        let compressed = compress_payload(&proto_enc[..])?;
        let key = CacheKey(CachePrefix::GuildConfigs, key.into().0);
        redis::Cmd::hset(key, Self::SUBKEY, compressed)
            .query_async(connection)
            .await?;
        return Ok(());
    }
}

pub trait CachedGuildConfig {
    const SUBKEY: u8;
}

macro_rules! guild_config {
    ($proto: ty, $key: expr) => {
        impl CachedGuildConfig for $proto {
            const SUBKEY: u8 = $key;
        }
    };
}

guild_config!(AutoConfig, 0_u8);
guild_config!(ModerationConfig, 1_u8);
guild_config!(LoggingConfig, 2_u8);
guild_config!(ValidationConfig, 3_u8);
guild_config!(MusicConfig, 4_u8);
guild_config!(AnnouncementConfig, 5_u8);
guild_config!(RoleConfig, 6_u8);
