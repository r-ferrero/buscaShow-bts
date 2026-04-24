#!/usr/bin/env python3
"""
Monitor de ingressos BTS - Ticketmaster + BuyTicket Brasil
Roda uma vez e envia email se encontrar ingressos disponíveis.
Agendado via GitHub Actions para rodar a cada 15 minutos.

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

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# Ticketmaster
TICKETMASTER_URL = "https://www.ticketmaster.com.br/event/bts-world-tour-arirang"
DATAS_TM = ["28 DE OUTUBRO", "30 DE OUTUBRO", "31 DE OUTUBRO"]

# BuyTicket Brasil (buyticketbrasil.com) — mercado secundário, filtro Pista < R$2000
EVENTO_LOCAL = "1775752182066x607042691407020000"
DATAS_BTB = {
    "28-10-2026": "1793242799000",
    "30-10-2026": "1793415599000",
    "31-10-2026": "1793501999000",
}
BTB_BASE_URL = "https://buyticketbrasil.com/evento/bts\u20132026worldtourarirang"
BTB_PRECO_MAX = 2000  # alertar se Pista abaixo desse valor

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def enviar_email(alertas: list[str]):
    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]
    password = os.environ["EMAIL_PASSWORD"]
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    use_ssl = os.environ.get("SMTP_TLS", "true").lower() != "false"

    corpo = "\n".join([
        "INGRESSOS BTS DISPONÍVEIS!",
        "=" * 40,
        "",
        *alertas,
        "",
        "Corra para garantir o seu ingresso!",
    ])

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = "🚨 INGRESSO BTS DISPONÍVEL"
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


# --- Ticketmaster (requests) ---

def checar_ticketmaster() -> list[str]:
    print("  Ticketmaster... ", end="", flush=True)
    try:
        r = requests.get(TICKETMASTER_URL, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        texto = soup.get_text()

        disponiveis = []
        for data in DATAS_TM:
            idx = texto.find(data)
            if idx == -1:
                continue
            trecho = texto[idx:idx + 200].upper()
            if "ESGOTADO" not in trecho:
                disponiveis.append(data)

        if disponiveis:
            print(f"DISPONÍVEL -> {disponiveis}")
        else:
            print("esgotado")

        return disponiveis

    except Exception as e:
        print(f"erro: {e}")
        return []


# --- BuyTicket Brasil (buyticketbrasil.com) ---

async def checar_btb(page, data: str, timestamp: str) -> list[str]:
    url = f"{BTB_BASE_URL}?data={timestamp}&evento_local={EVENTO_LOCAL}"
    print(f"  BTBrasil {data}... ", end="", flush=True)

    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        tipo_loc = page.locator("text=Tipo de ingresso")
        if await tipo_loc.count() == 0:
            print("sem ingressos")
            return []
        await tipo_loc.click()
        await page.wait_for_timeout(1500)

        texto = await page.evaluate("() => document.body.innerText")
        linhas = [l.strip() for l in texto.split("\n") if l.strip()]

        precos = {}
        for i, linha in enumerate(linhas):
            if linha == "Pista":
                proxima = linhas[i + 1] if i + 1 < len(linhas) else ""
                if proxima.startswith("R$"):
                    valor_str = proxima.replace("R$", "").replace(".", "").replace(",", ".").strip()
                    try:
                        precos["Pista"] = float(valor_str)
                    except ValueError:
                        pass

        alertas = []
        for setor, preco in precos.items():
            if preco < BTB_PRECO_MAX:
                alertas.append(f"{setor} R${preco:,.0f}".replace(",", "."))
                print(f"DISPONÍVEL {setor} R${preco:,.0f} (abaixo de R${BTB_PRECO_MAX})")

        if not alertas:
            pista_str = f"R${precos['Pista']:,.0f}".replace(",", ".") if "Pista" in precos else "sem ingresso"
            print(f"Pista {pista_str} (acima do limite)")

        return alertas

    except Exception as e:
        print(f"erro: {e}")
        return []


# --- Main ---

async def main():
    agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{agora}] Checando ingressos BTS...\n")

    alertas = []

    # Ticketmaster (rápido, sem browser)
    tm_disponiveis = checar_ticketmaster()
    for data in tm_disponiveis:
        alertas.append(f"[TICKETMASTER] {data} - {TICKETMASTER_URL}")

    # BuyTicket Brasil (mercado secundário, Pista < R$2000)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=HEADERS["User-Agent"])
        page = await context.new_page()

        for data, timestamp in DATAS_BTB.items():
            resultado = await checar_btb(page, data, timestamp)
            if resultado:
                link = f"{BTB_BASE_URL}?data={timestamp}&evento_local={EVENTO_LOCAL}"
                for item in resultado:
                    alertas.append(f"[BUYTICKETBRASIL] {data}: Pista {item} - {link}")

        await browser.close()

    if alertas:
        print("\nINGRESSOS ENCONTRADOS! Enviando email...")
        enviar_email(alertas)
    else:
        print("\nNenhum ingresso disponível.")

    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
