# Online provisioning only. Reuse the exact resulting image ID for offline runs.
FROM debian@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8
RUN dpkg --add-architecture armhf && apt-get update && \
    apt-get install -y --no-install-recommends \
      qemu-system-arm=1:7.2+dfsg-7+deb12u18+b3 qemu-utils=1:7.2+dfsg-7+deb12u18+b3 && \
    mkdir /inputs && cd /inputs && apt-get download \
      busybox-static:armhf=1:1.35.0-4+deb12u1+b1 \
      linux-image-6.1.0-56-armmp:armhf=6.1.192-2 && \
    echo '2f68dbeaebeda49f3cde34ac421d5add85fb1c837ff7c4a4971945340d9d66ff  busybox-static_1%3a1.35.0-4+deb12u1+b1_armhf.deb' | sha256sum -c - && \
    echo '6e64a25739d2f19b03e8bb0166a8c35582e9d83592f3f68e3305209733f49417  linux-image-6.1.0-56-armmp_6.1.192-2_armhf.deb' | sha256sum -c - && \
    dpkg-query -W > tools.packages
