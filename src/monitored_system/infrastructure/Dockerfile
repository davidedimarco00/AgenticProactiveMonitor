FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/monitored-system/src

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
    iputils-ping \
    dnsutils \
    jq \
    python3 \
    python3-pip \
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
# PYTHON APPLICATIONS
#############################
COPY src/api-gateway/requirements.txt /tmp/api-gateway-requirements.txt
COPY src/processing-service/requirements.txt /tmp/processing-service-requirements.txt
COPY src/data-service/requirements.txt /tmp/data-service-requirements.txt
COPY src/traffic-generator/requirements.txt /tmp/traffic-generator-requirements.txt

RUN pip3 install --no-cache-dir \
      -r /tmp/api-gateway-requirements.txt \
      -r /tmp/processing-service-requirements.txt \
      -r /tmp/data-service-requirements.txt \
      -r /tmp/traffic-generator-requirements.txt \
    && rm /tmp/*-requirements.txt

COPY src /opt/monitored-system/src

#############################
# OBSERVABILITY CONFIGURATION
#############################
COPY infrastructure/telegraf.conf /etc/telegraf/telegraf.conf
COPY infrastructure/fluent-bit.conf /etc/fluent-bit/fluent-bit.conf
COPY infrastructure/parsers.conf /etc/fluent-bit/parsers.conf
COPY infrastructure/entrypoint.sh /entrypoint.sh

RUN mkdir -p /var/log/machine /var/lib/notes /var/run/monitored-faults \
    && touch /var/log/machine/app.log \
             /var/log/machine/system.log \
             /var/log/machine/runtime.log \
    && chmod +x /entrypoint.sh

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=12 \
  CMD pgrep -x telegraf >/dev/null \
      && pgrep -f '[f]luent-bit' >/dev/null \
      && test -s /var/log/machine/app.log \
      && test -s /var/log/machine/system.log \
      || exit 1

ENTRYPOINT ["/entrypoint.sh"]
