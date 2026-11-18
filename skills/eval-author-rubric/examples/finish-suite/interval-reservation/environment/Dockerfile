FROM python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN mkdir -p /app /work && chown 65534:65534 /work && chmod 755 /app /work
COPY case.json /app/case.json
RUN chown root:root /app/case.json && chmod 444 /app/case.json
WORKDIR /work
