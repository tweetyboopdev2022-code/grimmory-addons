FROM python:3.12-alpine
RUN adduser -D -u 10001 wishlist && mkdir /data && chown wishlist /data
WORKDIR /app
# the one dependency: encryption for Chrome notifications (prebuilt wheel, nothing to compile)
RUN pip install --no-cache-dir cryptography==44.0.2
COPY wishlist /app/wishlist
RUN chmod -R a+rX /app          # whatever permissions the files had on the host
USER wishlist
ENV PORT=8080 WISHLIST_DB=/data/wishlist.db PYTHONUNBUFFERED=1
VOLUME /data
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/health || exit 1
CMD ["python", "-m", "wishlist.server"]
