FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app


# PDF export is optional at runtime, but the hosted studio should have it.
# latex-recommended supplies geometry/hyperref, lang-chinese supplies ctex;
# Noto CJK + fontset=ubuntu is the Debian trixie font set ctex expects.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-xetex texlive-lang-chinese texlive-latex-recommended texlive-fonts-recommended fonts-noto-cjk \
        tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

# The studio is a shared instance: keep visitor API keys in process memory only
# and never write them into the container filesystem.
ENV PAPERPILOT_XELATEX=/usr/bin/xelatex \
    PAPERPILOT_CTEX_FONTSET=ubuntu \
    PAPERPILOT_LLM_PERSIST_KEY=0

COPY paperpilot/ /app/paperpilot/

RUN pip install --no-cache-dir -r /app/paperpilot/requirements.txt

WORKDIR /app/paperpilot

EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
