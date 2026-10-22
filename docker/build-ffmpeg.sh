#!/bin/sh
set -eu
# Keep audio used by mp3/m4a/wav/ogg inputs and the AAC -> MPEG-TS HLS path.
# Run from the extracted, checksum-verified upstream FFmpeg source directory.
./configure \
    --prefix=/opt/ffmpeg \
    --disable-everything --disable-autodetect --disable-network \
    --disable-doc --disable-debug --disable-ffplay --disable-ffprobe \
    --disable-avdevice --disable-swscale \
    --enable-small --enable-shared --disable-static --enable-ffmpeg \
    --enable-avfilter --enable-swresample \
    --enable-protocol=file,pipe \
    --enable-demuxer=mp3,mov,wav,ogg \
    --enable-decoder=mp3,mp3float,aac,aac_fixed,alac,vorbis,opus,flac,pcm_s8,pcm_u8,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_s32be,pcm_f32le,pcm_f32be,pcm_f64le,pcm_f64be,pcm_alaw,pcm_mulaw,adpcm_ima_wav,adpcm_ms \
    --enable-parser=mpegaudio,aac,aac_latm,vorbis,opus,flac \
    --enable-encoder=aac \
    --enable-muxer=hls,mpegts,adts \
    --enable-bsf=aac_adtstoasc \
    --enable-filter=abuffer,abuffersink,aresample,aformat,anull,atrim,asetpts
make -j"${FFMPEG_BUILD_JOBS:-2}"
make install
mkdir -p /opt/ffmpeg/share/licenses/ffmpeg
cp COPYING.LGPLv2.1 LICENSE.md /opt/ffmpeg/share/licenses/ffmpeg/
cp /build/build-ffmpeg.sh /opt/ffmpeg/share/licenses/ffmpeg/build-ffmpeg.sh
printf '%s\n' \
    'FFmpeg 8.1.3, unmodified upstream source; configured for SONORA audio HLS.' \
    'Source: https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz' \
    'SHA256: 7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3' \
    'Build recipe: build-ffmpeg.sh (also in the project docker/ directory).' \
    > /opt/ffmpeg/share/licenses/ffmpeg/SOURCE.txt
