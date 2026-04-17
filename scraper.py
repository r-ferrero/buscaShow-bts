#!/usr/bin/env python3
"""
Monitor de ingressos BTS - BuyTicket Brasil
Roda uma vez e envia email se encontrar ingressos disponíveis.
Agendado via GitHub Actions para rodar a cada 5 minutos.

Variáveis de ambiente necessárias:
    EMAIL_FROM      - email remetente
    EMAIL_PASSWORD  - senha do SMTP
    EMAIL_TO        - email que recebe o alerta

Variáveis opcionais (defaults para Gmail):
    SMTP_HOST       - servidor SMTP (padrão: smtp.gmail.com)
    SMTP_PORT       - porta (padrão: 465)
    SMTP_TLS        - usar SSL? true/false (padrão: true). Use false para STARTTLS (porta 587)
"""

import asyncio
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from playwright.async_api import async_playwright

DATAS = ["28-10-2026", "30-10-2026", "31-10-2026"]
BASE_URL = "https://bts.buyticketbrasil.com/ingressos?data="
SETORES_CONHECIDOS = ["Cadeira Inferior", "Arquibancada", "Pista", "Cadeira Superior"]


def enviar_email(disponiveis_por_data: dict[str, list[str]]):
    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]
    password = os.environ["EMAIL_PASSWORD"]
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    # SMTP_TLS=false para desativar SSL (ex: porta 587 com STARTTLS)
    use_ssl = os.environ.get("SMTP_TLS", "true").lower() != "false"

    linhas = []
    for data, setores in disponiveis_por_data.items():
        linhas.append(f"Data {data}:")
        for s in setores:
            linhas.append(f"  - {s}")
        linhas.append(f"  Link: {BASE_URL}{data}")
        linhas.append("")

    corpo = "\n".join([
        "INGRESSOS DISPONÍVEIS NO BUYTICKET BTS!",
        "=" * 40,
        "",
        *linhas,
        "Corra para garantir o seu ingresso!",
    ])

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = "🚨 INGRESSO BTS DISPONÍVEL - BuyTicket"
    msg.attach(MIMEText(corpo, "plain", "utf-8"))

    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
            smtp.login(email_from, password)
            smtp.sendmail(email_from, email_to, msg.as_string())
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as smtp:
            smtp.starttls()
            smtp.login(email_from, password)
            smtp.sendmail(email_from, email_to, msg.as_string())

    print(f"  Email enviado para {email_to}")


async def checar_data(page, data: str) -> list[str]:
    url = BASE_URL + data
    print(f"  {data}... ", end="", flush=True)

    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1500)

        texto = await page.evaluate("() => document.body.innerText")
        linhas = [l.strip() for l in texto.split("\n") if l.strip()]

        try:
            inicio = linhas.index("Escolha o setor") + 1
            fim = linhas.index("PASSO 2", inicio)
            secao = linhas[inicio:fim]
        except ValueError:
            secao = linhas

        disponiveis = []
        for i, linha in enumerate(secao):
            if linha in SETORES_CONHECIDOS:
                proxima = secao[i + 1] if i + 1 < len(secao) else ""
                if proxima.upper() != "ESGOTADO":
                    status = proxima if proxima else "disponível"
                    disponiveis.append(f"{linha} ({status})")

        if disponiveis:
            print(f"DISPONÍVEL -> {disponiveis}")
        else:
            print("esgotado")

        return disponiveis

    except Exception as e:
        print(f"erro: {e}")
        return []


async def main():
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{agora}] Checando ingressos BTS...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        disponiveis_por_data = {}
        for data in DATAS:
            resultado = await checar_data(page, data)
            if resultado:
                disponiveis_por_data[data] = resultado

        await browser.close()

    if disponiveis_por_data:
        print("\nINGRESSOS ENCONTRADOS! Enviando email...")
        enviar_email(disponiveis_por_data)
        sys.exit(0)
    else:
        print("Nenhum ingresso disponível.")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
