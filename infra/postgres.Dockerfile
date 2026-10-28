ARG POSTGRES_IMAGE
FROM ${POSTGRES_IMAGE}
# The official image's Go-built gosu embeds vulnerable Go stdlib versions.
# Use Alpine's small C implementation without changing PostgreSQL or its data format.
RUN rm /usr/local/bin/gosu /usr/local/bin/su-exec \
    && apk add --no-cache su-exec=0.3-r0 \
    && sed -i 's/exec gosu postgres/exec \/sbin\/su-exec postgres/' /usr/local/bin/docker-entrypoint.sh
