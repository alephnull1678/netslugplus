FROM debian:bookworm-slim

ARG DEVKITPPC_URL="https://wii.leseratte10.de/devkitPro/devkitPPC/r27%20(2014)/devkitPPC_r27-x86_64-linux.tar.bz2"
ARG LIBOGC_URL="https://wii.leseratte10.de/devkitPro/libogc/libogc_1.8.12%20(2014-04-02)/libogc-1.8.12.tar.bz2"
ARG LIBFAT_URL="https://wii.leseratte10.de/devkitPro/libfat/libfat_1.0.13%20(2014)/libfat-ogc-1.0.13.tar.bz2"
ARG MXML_URL="https://wii.leseratte10.de/devkitPro/portlibs/ppc-mxml-2.11-2-any.pkg.tar.xz"

ENV DEVKITPRO=/opt/devkitpro
ENV DEVKITPPC=/opt/devkitpro/devkitPPC
ENV PORTLIBS=/opt/devkitpro/portlibs/ppc
ENV PATH=/opt/devkitpro/devkitPPC/bin:/opt/devkitpro/portlibs/ppc/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bzip2 \
      ca-certificates \
      curl \
      file \
      make \
      xz-utils \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p "$DEVKITPRO" "$PORTLIBS" /tmp/devkitpro-downloads \
 && curl -fL --retry 5 --connect-timeout 20 "$DEVKITPPC_URL" -o /tmp/devkitpro-downloads/devkitPPC.tar \
 && curl -fL --retry 5 --connect-timeout 20 "$LIBOGC_URL" -o /tmp/devkitpro-downloads/libogc.tar \
 && curl -fL --retry 5 --connect-timeout 20 "$LIBFAT_URL" -o /tmp/devkitpro-downloads/libfat.tar \
 && curl -fL --retry 5 --connect-timeout 20 "$MXML_URL" -o /tmp/devkitpro-downloads/mxml.tar \
 && tar -xf /tmp/devkitpro-downloads/devkitPPC.tar -C "$DEVKITPRO" \
 && if [ ! -x "$DEVKITPPC/bin/powerpc-eabi-gcc" ]; then \
      found_gcc="$(find "$DEVKITPRO" -type f -path "*/devkitPPC/bin/powerpc-eabi-gcc" -print -quit)" \
      && [ -n "$found_gcc" ] \
      && found_devkitppc="$(dirname "$(dirname "$found_gcc")")" \
      && rm -rf "$DEVKITPPC" \
      && mv "$found_devkitppc" "$DEVKITPPC"; \
    fi \
 && mkdir -p "$DEVKITPRO/libogc" \
 && tar -xf /tmp/devkitpro-downloads/libogc.tar -C "$DEVKITPRO/libogc" \
 && tar -xf /tmp/devkitpro-downloads/libfat.tar -C "$DEVKITPRO/libogc" \
 && mkdir -p /tmp/devkitpro-downloads/mxml \
 && tar -xf /tmp/devkitpro-downloads/mxml.tar -C /tmp/devkitpro-downloads/mxml \
 && if [ -d /tmp/devkitpro-downloads/mxml/opt/devkitpro/portlibs/ppc ]; then \
      cp -a /tmp/devkitpro-downloads/mxml/opt/devkitpro/portlibs/ppc/. "$PORTLIBS/"; \
    else \
      cp -a /tmp/devkitpro-downloads/mxml/. "$PORTLIBS/"; \
    fi \
 && rm -rf /tmp/devkitpro-downloads

WORKDIR /work

CMD ["make", "release"]
