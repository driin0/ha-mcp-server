# Le dipendenze si compilano in uno stage separato: gcc e i sorgenti degli
# header restano fuori dall'immagine finale, che dimezza di dimensione.
FROM python:3.13-alpine AS builder

# python3-dev deliberately excluded: it pulls in Alpine's own Python
# interpreter (currently 3.14, into a 3.13 image), redundant since pip
# already resolves build headers through the running interpreter's own
# sysconfig - the 3.13 headers this base image ships - and a latent ABI
# hazard for no benefit, since only /usr/local/lib/python3.13/site-packages
# is copied into the final stage below.
RUN apk add --no-cache gcc musl-dev

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.13-alpine

# Alpine e Python di questa immagine devono restare allineati alla base usata
# dall'app Home Assistant (ghcr.io/home-assistant/base-python:3.13-alpineX.Y):
# quella copia da qui i pacchetti già compilati invece di reinstallarli, e le
# estensioni native (pydantic_core, cryptography, rpds, websockets) funzionano
# solo se musl e la minor di Python coincidono.
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages

WORKDIR /app

COPY server.py web.py stats.py ./
COPY tools/ tools/
COPY requirements.txt LICENSE ./

# Se un aggiornamento di base rompesse l'ABI, meglio scoprirlo qui che al primo
# avvio: senza questa riga l'immagine si costruirebbe comunque.
RUN python3 -c "import mcp, httpx, websockets, dotenv, pydantic_core, cryptography"

RUN adduser -u 10001 -D -H -s /sbin/nologin app && chown -R app:app /app
USER app

ENV MCP_PORT=47821 \
    WEB_PORT=47822

EXPOSE 47821 47822

CMD ["python3", "server.py"]
