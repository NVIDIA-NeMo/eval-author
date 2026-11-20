FROM python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
RUN mkdir -p /app /work /run/refund /var/lib/refund && chown 65534:65534 /work && chmod 755 /app /work /run/refund && chmod 700 /var/lib/refund
COPY contract.json /app/contract.json
COPY ledger.py server.py /app/
RUN chmod 444 /app/contract.json /app/ledger.py /app/server.py
WORKDIR /work
ENTRYPOINT ["python", "-B", "/app/server.py"]
