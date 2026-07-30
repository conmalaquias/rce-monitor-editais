"""
═══════════════════════════════════════════════════════════════════
  RCE Engenharia — Monitor Inteligente de Editais e Licitações
═══════════════════════════════════════════════════════════════════

  O que ele faz:
   1. Lê a lista de sites em  sites.txt
   2. Lê as palavras de obra em  palavras-chave.txt
   3. Acessa cada página e extrai os editais (links/PDFs)
   4. Identifica quais editais são NOVOS desde a última verificação
   5. Filtra: só considera os que falam de OBRA (palavras-chave)
   6. Envia um e-mail organizado com nome + link de cada edital novo

  Uso:
   python monitor.py            -> execução normal (usada pelo GitHub)
   python monitor.py --teste    -> roda um autoteste, sem internet e sem e-mail
═══════════════════════════════════════════════════════════════════
"""

import os
import re
import sys
import json
import time
import smtplib
import unicodedata
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

# Suprime o aviso "InsecureRequestWarning" que aparece quando usamos
# verify=False como último recurso para sites com certificado quebrado.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ───────────────────────────────────────────────
#  CONFIGURAÇÕES
# ───────────────────────────────────────────────

DESTINATARIOS = [
    "conrado.malaquias@rceengenharia.eng.br",
    "nailson@rceengenharia.eng.br",
]

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")

ARQUIVO_SITES = "sites.txt"
ARQUIVO_PALAVRAS = "palavras-chave.txt"
ARQUIVO_ESTADO = "estado/estado.json"

TIMEOUT = 25
TENTATIVAS = 2          # nº de tentativas por site antes de desistir
ESPERA_ENTRE_TENTATIVAS = 4  # segundos

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
}

# Quantidade de falhas seguidas para um site ser sinalizado como
# "possivelmente quebrado" no relatório (mesmo sem editais novos)
LIMITE_FALHAS_ALERTA = 3

# Esquemas e domínios que nunca são editais
ESQUEMAS_IGNORAR = ("mailto:", "tel:", "javascript:", "whatsapp:")
DOMINIOS_IGNORAR = (
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "linkedin.com", "wa.me", "google.com",
)


# ───────────────────────────────────────────────
#  LEITURA DOS ARQUIVOS DE CONFIGURAÇÃO
# ───────────────────────────────────────────────

def normalizar(texto):
    """Remove acentos e coloca em minúsculas, para comparação robusta."""
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto


def ler_sites():
    """Lê sites.txt no formato:  Nome | URL | Categoria"""
    sites = []
    if not os.path.exists(ARQUIVO_SITES):
        print(f"❌ Arquivo {ARQUIVO_SITES} não encontrado.")
        return sites

    with open(ARQUIVO_SITES, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            partes = [p.strip() for p in linha.split("|")]
            if len(partes) == 1:
                nome, url, categoria = partes[0], partes[0], "Geral"
            elif len(partes) == 2:
                nome, url, categoria = partes[0], partes[1], "Geral"
            else:
                nome, url, categoria = partes[0], partes[1], partes[2]
            if url.lower().startswith("http"):
                sites.append({"nome": nome, "url": url, "categoria": categoria})
    return sites


def ler_palavras():
    """Lê palavras-chave.txt (uma por linha) e devolve já normalizadas."""
    palavras = []
    if not os.path.exists(ARQUIVO_PALAVRAS):
        print(f"❌ Arquivo {ARQUIVO_PALAVRAS} não encontrado.")
        return palavras

    with open(ARQUIVO_PALAVRAS, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            palavras.append(normalizar(linha))
    return palavras


def carregar_estado():
    if os.path.exists(ARQUIVO_ESTADO):
        try:
            with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salvar_estado(estado):
    os.makedirs("estado", exist_ok=True)
    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# ───────────────────────────────────────────────
#  INTELIGÊNCIA: extração e filtragem de editais
# ───────────────────────────────────────────────

def contem_palavra_obra(texto, palavras):
    """
    Verifica se o texto contém alguma palavra-chave de obra.
    Usa fronteira de palavra no início (\\b) para evitar falsos
    positivos como 'sobra' batendo com 'obra'.
    Devolve a lista de termos que bateram.
    """
    alvo = normalizar(texto)
    encontradas = []
    for p in palavras:
        # \b no início + a raiz; assim "reform" pega "reforma/reformas"
        if re.search(r"\b" + re.escape(p), alvo):
            encontradas.append(p)
    return encontradas


def extrair_editais(html, base_url, palavras):
    """
    Extrai da página os links que parecem editais E que falam de obra.
    Devolve uma lista de dicionários: {titulo, url, contexto, termos}.
    """
    soup = BeautifulSoup(html, "html.parser")
    vistos = set()
    editais = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()

        # Ignora esquemas e âncoras
        if href.startswith("#") or href.lower().startswith(ESQUEMAS_IGNORAR):
            continue

        url_completa = urljoin(base_url, href)
        dominio = urlparse(url_completa).netloc.lower()
        if any(d in dominio for d in DOMINIOS_IGNORAR):
            continue
        if not url_completa.lower().startswith("http"):
            continue

        # Texto do link
        texto_link = a.get_text(" ", strip=True)

        # Texto do "recipiente" (linha da tabela, item de lista, etc.)
        recipiente = a.find_parent(["li", "tr", "div", "p", "article", "td"])
        texto_contexto = recipiente.get_text(" ", strip=True) if recipiente else texto_link

        # Junta tudo que ajuda a decidir: texto do link + contexto + nome do arquivo
        material = f"{texto_link} {texto_contexto} {href}"

        termos = contem_palavra_obra(material, palavras)
        if not termos:
            continue

        # Evita duplicar o mesmo link na mesma página
        if url_completa in vistos:
            continue
        vistos.add(url_completa)

        titulo = texto_link or (texto_contexto[:80] if texto_contexto else "(ver link)")
        editais.append({
            "titulo": titulo[:200],
            "url": url_completa,
            "contexto": texto_contexto[:260],
            "termos": sorted(set(termos)),
        })

    return editais


def baixar(url):
    """
    Baixa a página. Devolve o HTML ou None em caso de erro.

    Estratégia:
     - Tenta algumas vezes (TENTATIVAS) com uma pequena espera entre
       elas — resolve falhas passageiras de rede/timeout.
     - Se o erro for de certificado SSL (comum em sites públicos com
       certificado mal configurado, ex: hostname mismatch), tenta de
       novo SEM verificar o certificado. Isso é aceitável aqui porque
       só estamos LENDO uma página pública de licitações, sem enviar
       nenhum dado sensível.
    """
    ultimo_erro = None

    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text

        except requests.exceptions.SSLError as e:
            print(f"   ⚠️  Certificado SSL inválido — tentando mesmo assim (site público, somente leitura): {e}")
            try:
                resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or resp.encoding
                return resp.text
            except Exception as e2:
                ultimo_erro = e2

        except Exception as e:
            ultimo_erro = e
            if tentativa < TENTATIVAS:
                print(f"   ↻ Tentativa {tentativa} falhou ({e}). Tentando novamente em {ESPERA_ENTRE_TENTATIVAS}s...")
                time.sleep(ESPERA_ENTRE_TENTATIVAS)

    print(f"   ⚠️  Falha ao acessar após {TENTATIVAS} tentativa(s): {ultimo_erro}")
    return None


# ───────────────────────────────────────────────
#  E-MAIL
# ───────────────────────────────────────────────

def bloco_sites_com_problema(sites_com_problema):
    """Gera um bloco HTML de aviso para sites falhando há várias execuções."""
    if not sites_com_problema:
        return ""
    itens = ""
    for s in sites_com_problema:
        itens += f"""
        <li style="margin-bottom:6px;">
            <strong>{s['nome']}</strong> — falhando há {s['falhas']} execuções seguidas
            (<a href="{s['url']}" style="color:#c8102e;">{s['url']}</a>)
        </li>
        """
    return f"""
    <div style="margin-top:8px; margin-bottom:24px; padding:16px; background:#fff8e6; border-left:4px solid #e0a800; border-radius:4px;">
        <p style="margin:0 0 8px 0; font-size:13px; color:#555;">
            ⚠️ <strong>Atenção:</strong> os sites abaixo não estão sendo monitorados corretamente
            (erro de acesso persistente). Editais publicados neles podem estar passando despercebidos.
            Vale checar manualmente ou avisar quem cuida do robô.
        </p>
        <ul style="margin:0; padding-left:20px; font-size:13px; color:#555;">
            {itens}
        </ul>
    </div>
    """


def montar_email(novidades, sites_com_problema=None):
    """Monta o corpo HTML do e-mail a partir das novidades agrupadas por site."""
    agora = datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y às %H:%M")
    total = sum(len(v["editais"]) for v in novidades)
    aviso_falhas = bloco_sites_com_problema(sites_com_problema)

    blocos = ""
    for v in novidades:
        site = v["site"]
        linhas = ""
        url_pagina = site["url"]
        for e in v["editais"]:
            termos = ", ".join(e["termos"])
            linhas += f"""
            <div style="padding:14px 0; border-bottom:1px solid #eee;">
                <a href="{e['url']}" style="color:#c8102e; font-weight:bold; font-size:15px; text-decoration:none;">
                    {e['titulo']}
                </a>
                <div style="margin-top:8px; font-size:12px; color:#888;">
                    🔎 Identificado por: <em>{termos}</em>
                </div>
                <div style="margin-top:6px; font-size:12px; color:#555;">
                    📄 <strong>Link do edital:</strong>
                    <a href="{e['url']}" style="color:#c8102e; word-break:break-all;">{e['url']}</a>
                </div>
                <div style="margin-top:4px; font-size:12px; color:#555;">
                    🌐 <strong>Página de origem:</strong>
                    <a href="{url_pagina}" style="color:#666; word-break:break-all;">{url_pagina}</a>
                </div>
            </div>
            """
        blocos += f"""
        <div style="margin-bottom:28px;">
            <div style="background:#14141a; padding:12px 18px; border-radius:6px 6px 0 0;">
                <span style="color:#fff; font-weight:bold; font-size:15px;">{site['nome']}</span>
                <span style="color:#c8102e; font-size:12px; margin-left:8px;">{site['categoria']}</span>
            </div>
            <div style="padding:4px 18px; background:#fafafa; border:1px solid #eee; border-top:none; border-radius:0 0 6px 6px;">
                {linhas}
            </div>
        </div>
        """

    plural = "novo edital de obra" if total == 1 else "novos editais de obra"

    return f"""
    <html><body style="font-family:Arial,Helvetica,sans-serif; background:#f0f0f3; padding:24px; margin:0;">
        <div style="max-width:720px; margin:0 auto; background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 3px 14px rgba(0,0,0,0.08);">

            <div style="background:#0f0f14; padding:28px 32px;">
                <h1 style="color:#fff; margin:0; font-size:21px;">🔔 {total} {plural}</h1>
                <p style="color:#c8102e; margin:6px 0 0 0; font-size:13px; letter-spacing:0.5px;">
                    RCE ENGENHARIA · MONITOR DE LICITAÇÕES · {agora}
                </p>
            </div>

            <div style="padding:28px 32px;">
                <p style="color:#444; font-size:14px; margin-top:0;">
                    O monitoramento de hoje identificou os editais abaixo, filtrados
                    automaticamente por conterem termos de obra, reforma ou construção:
                </p>

                {blocos}

                {aviso_falhas}

                <div style="margin-top:8px; padding:16px; background:#fff8f8; border-left:4px solid #c8102e; border-radius:4px;">
                    <p style="margin:0; font-size:13px; color:#555;">
                        ⚡ <strong>Ação:</strong> abra cada edital, confirme o objeto e o prazo,
                        e havendo aderência ao perfil da RCE, acione a instituição o quanto antes.
                    </p>
                </div>
            </div>

            <div style="background:#f9f9f9; padding:18px 32px; border-top:1px solid #eee;">
                <p style="margin:0; font-size:11px; color:#999;">
                    Alerta gerado automaticamente pelo Monitor de Editais da RCE Engenharia e Consultoria.<br>
                    Contato comercial: <a href="mailto:comercial@rceengenharia.eng.br" style="color:#c8102e;">comercial@rceengenharia.eng.br</a>
                </p>
            </div>

        </div>
    </body></html>
    """


def _enviar(assunto, corpo_html):
    """Função interna que efetivamente conecta no Gmail e envia o e-mail."""
    if not GMAIL_USER or not GMAIL_PASS:
        print("⚠️  Credenciais de e-mail ausentes (GMAIL_USER / GMAIL_PASS). E-mail não enviado.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = f"RCE Monitor <{GMAIL_USER}>"
    msg["To"] = ", ".join(DESTINATARIOS)
    msg.attach(MIMEText(corpo_html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.sendmail(GMAIL_USER, DESTINATARIOS, msg.as_string())
        print(f"✅ E-mail enviado para: {', '.join(DESTINATARIOS)}")
    except Exception as e:
        print(f"❌ Erro ao enviar e-mail: {e}")


def enviar_email(novidades, sites_com_problema=None):
    total = sum(len(v["editais"]) for v in novidades)
    corpo = montar_email(novidades, sites_com_problema)
    assunto = f"🔔 {total} novo(s) edital(is) de obra — RCE Monitor"
    _enviar(assunto, corpo)


def enviar_alerta_falhas(sites_com_problema):
    """
    Envia um e-mail curto e dedicado quando não há editais novos, mas
    existem sites falhando há várias execuções seguidas — para que
    isso nunca fique invisível dentro dos logs do GitHub Actions.
    """
    agora = datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y às %H:%M")
    aviso = bloco_sites_com_problema(sites_com_problema)
    corpo = f"""
    <html><body style="font-family:Arial,Helvetica,sans-serif; background:#f0f0f3; padding:24px; margin:0;">
        <div style="max-width:720px; margin:0 auto; background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 3px 14px rgba(0,0,0,0.08);">
            <div style="background:#0f0f14; padding:28px 32px;">
                <h1 style="color:#fff; margin:0; font-size:21px;">⚠️ Sites do monitor com falha persistente</h1>
                <p style="color:#c8102e; margin:6px 0 0 0; font-size:13px; letter-spacing:0.5px;">
                    RCE ENGENHARIA · MONITOR DE LICITAÇÕES · {agora}
                </p>
            </div>
            <div style="padding:28px 32px;">
                <p style="color:#444; font-size:14px; margin-top:0;">
                    Nenhum edital novo foi identificado hoje, mas os sites abaixo estão
                    falhando repetidamente — ou seja, editais publicados neles podem
                    não estar sendo capturados pelo robô.
                </p>
                {aviso}
            </div>
        </div>
    </body></html>
    """
    _enviar("⚠️ RCE Monitor — sites com falha persistente", corpo)


# ───────────────────────────────────────────────
#  EXECUÇÃO PRINCIPAL
# ───────────────────────────────────────────────

def executar():
    print(f"\n{'='*64}")
    print(f"  RCE MONITOR — {datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M')} (Brasília)")
    print(f"{'='*64}\n")

    sites = ler_sites()
    palavras = ler_palavras()
    estado = carregar_estado()
    falhas_anteriores = estado.get("_falhas", {})

    print(f"📋 {len(sites)} site(s) na lista | {len(palavras)} palavra(s)-chave de obra\n")

    novidades = []        # o que vai pro e-mail
    novo_estado = {}
    novo_falhas = {}
    sites_com_problema = []   # sites falhando há LIMITE_FALHAS_ALERTA execuções ou mais

    for site in sites:
        nome, url = site["nome"], site["url"]
        print(f"🔍 {nome}")

        html = baixar(url)
        if html is None:
            # Mantém o estado anterior para não gerar falso alerta
            novo_estado[url] = estado.get(url, [])
            novo_falhas[url] = falhas_anteriores.get(url, 0) + 1
            if novo_falhas[url] >= LIMITE_FALHAS_ALERTA:
                sites_com_problema.append({
                    "nome": nome, "url": url, "falhas": novo_falhas[url]
                })
                print(f"   🛑 Este site falha há {novo_falhas[url]} execuções seguidas — pode estar quebrado.")
            print()
            continue

        novo_falhas[url] = 0

        editais = extrair_editais(html, url, palavras)
        urls_atuais = [e["url"] for e in editais]

        vistos_antes = set(estado.get(url, []))

        # Primeira vez vendo este site: registra baseline, NÃO alerta
        # (evita um e-mail gigante com todo o histórico no primeiro dia)
        if url not in estado:
            print(f"   🆕 Primeira leitura — {len(editais)} edital(is) de obra registrados como base (sem alerta).\n")
            novo_estado[url] = urls_atuais
            continue

        # Editais novos = relevantes que ainda não tínhamos visto
        novos = [e for e in editais if e["url"] not in vistos_antes]

        if novos:
            print(f"   🚨 {len(novos)} EDITAL(IS) DE OBRA NOVO(S)!")
            for e in novos:
                print(f"      • {e['titulo']}  [{', '.join(e['termos'])}]")
            novidades.append({"site": site, "editais": novos})
        else:
            print(f"   ✅ Sem editais de obra novos ({len(editais)} relevante(s) no total).")

        # Acumula: mantém tudo que já foi visto + o que há agora
        novo_estado[url] = sorted(vistos_antes.union(urls_atuais))
        print()

    novo_estado["_falhas"] = novo_falhas
    salvar_estado(novo_estado)

    if novidades:
        total = sum(len(v["editais"]) for v in novidades)
        print(f"{'='*64}")
        print(f"  {total} edital(is) de obra novo(s). Enviando e-mail...")
        print(f"{'='*64}\n")
        enviar_email(novidades, sites_com_problema)
    elif sites_com_problema:
        print(f"{'='*64}")
        print(f"  Nenhum edital novo, mas {len(sites_com_problema)} site(s) com falha persistente. Enviando alerta...")
        print(f"{'='*64}\n")
        enviar_alerta_falhas(sites_com_problema)
    else:
        print("✅ Nenhum edital de obra novo hoje. Nenhum e-mail enviado.\n")


# ───────────────────────────────────────────────
#  AUTOTESTE (sem internet, sem e-mail)
# ───────────────────────────────────────────────

def autoteste():
    print(f"\n{'='*64}")
    print("  AUTOTESTE — validando o filtro inteligente (sem internet)")
    print(f"{'='*64}\n")

    palavras = ler_palavras()

    # Página de teste simulando um portal de licitações de hospital,
    # misturando editais de OBRA com editais que NÃO interessam.
    html_teste = """
    <html><body>
      <table>
        <tr><td><a href="/docs/edital-02-2026.pdf">Edital 02/2026 — Construção do Centro de Cuidado Integral</a></td></tr>
        <tr><td><a href="/docs/edital-01-2026.pdf">Edital 01/2026 — Contratação de Obra de Ampliação da Entrada de Energia Elétrica</a></td></tr>
        <tr><td><a href="/docs/residencia-2026.pdf">Edital — Processo Seletivo de Residência Médica 2026</a></td></tr>
        <tr><td><a href="/docs/cotacao-ressonancia.pdf">Cotação — Aquisição de equipamento de ressonância magnética</a></td></tr>
        <tr><td><a href="/docs/pregao-telhado.pdf">Pregão 14/2026 — Reforma do telhado e impermeabilização do Bloco C</a></td></tr>
        <tr><td><a href="/docs/medicamentos.pdf">Cotação — Aquisição de medicamentos e materiais hospitalares</a></td></tr>
        <tr><td><a href="/docs/retrofit-uti.pdf">Concorrência — Retrofit da UTI e instalações de climatização</a></td></tr>
        <tr><td><a href="https://facebook.com/hospital">Siga-nos no Facebook</a></td></tr>
      </table>
    </body></html>
    """

    editais = extrair_editais(html_teste, "https://hospital-teste.com.br/licitacoes", palavras)

    print(f"De 8 links na página, o robô considerou {len(editais)} como editais de OBRA:\n")
    for e in editais:
        print(f"  ✅ {e['titulo']}")
        print(f"        termos detectados: {', '.join(e['termos'])}")
        print(f"        link: {e['url']}\n")

    esperado_obra = {
        "Edital 02/2026 — Construção do Centro de Cuidado Integral",
        "Edital 01/2026 — Contratação de Obra de Ampliação da Entrada de Energia Elétrica",
        "Pregão 14/2026 — Reforma do telhado e impermeabilização do Bloco C",
        "Concorrência — Retrofit da UTI e instalações de climatização",
    }
    obtidos = {e["titulo"] for e in editais}

    print(f"{'─'*64}")
    if obtidos == esperado_obra:
        print("  ✅ TESTE PASSOU: pegou exatamente os 4 editais de obra e")
        print("     ignorou residência médica, ressonância, medicamentos e Facebook.")
    else:
        print("  ⚠️  Divergência:")
        print(f"     faltaram: {esperado_obra - obtidos}")
        print(f"     sobraram: {obtidos - esperado_obra}")
    print(f"{'─'*64}\n")


if __name__ == "__main__":
    if "--teste" in sys.argv:
        autoteste()
    else:
        executar()
