FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

#############################
# BASE PACKAGES
#############################
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    gnupg \
    ca-certificates \
    lsb-release \
    procps \
    sysstat \
    net-tools \
    iproute2 \
    dnsutils \
    jq \
    && rm -rf /var/lib/apt/lists/*

#############################
# INSTALL TELEGRAF
#############################
RUN wget -q https://repos.influxdata.com/influxdata-archive.key -O /tmp/influxdata-archive.key \
    && cat /tmp/influxdata-archive.key | gpg --dearmor > /usr/share/keyrings/influxdata-archive.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/ubuntu jammy stable" > /etc/apt/sources.list.d/influxdata.list \
    && apt-get update \
    && apt-get install -y telegraf \
    && rm -rf /var/lib/apt/lists/* /tmp/influxdata-archive.key

#############################
# INSTALL FLUENT BIT
#############################
RUN curl -fsSL https://packages.fluentbit.io/fluentbit.key | gpg --dearmor > /usr/share/keyrings/fluentbit-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/fluentbit-keyring.gpg] https://packages.fluentbit.io/ubuntu/jammy jammy main" > /etc/apt/sources.list.d/fluent-bit.list \
    && apt-get update \
    && apt-get install -y fluent-bit \
    && rm -rf /var/lib/apt/lists/*

#############################
# CONFIGURATION
#############################
COPY telegraf.conf /etc/telegraf/telegraf.conf
COPY fluent-bit.conf /etc/fluent-bit/fluent-bit.conf
COPY parsers.conf /etc/fluent-bit/parsers.conf
COPY entrypoint.sh /entrypoint.sh

RUN mkdir -p /var/log/machine \
    && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]