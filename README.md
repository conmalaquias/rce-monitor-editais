# 🔔 RCE Monitor Inteligente de Editais

Monitora páginas de licitações de hospitais, Sistema S e instituições
e avisa por e-mail **somente quando surge um edital de obra** (construção,
reforma, retrofit, ampliação etc.), ignorando residência médica, compra de
equipamento, medicamento e outros editais que não interessam à RCE.

**Custo:** R$ 0,00 — roda no GitHub Actions (gratuito).
**Frequência:** 1x por dia, às 08h00 (horário de Brasília).

---

## Como a "inteligência" funciona

1. Lê a lista de sites em **`sites.txt`**
2. Lê as palavras de obra em **`palavras-chave.txt`**
3. Abre cada página e extrai os links de editais (PDFs e páginas de detalhe)
4. Compara com a verificação anterior para achar **o que é novo**
5. Mantém só os editais novos que contêm **palavra de obra**
6. Envia um e-mail organizado por instituição, com **nome + link** de cada edital
   e quais termos dispararam o alerta

Se nada de novo (e relevante) aparecer, **nenhum e-mail é enviado** — sem spam.

---

## 📂 Os arquivos que você vai editar

### `sites.txt` — quais sites monitorar
Formato simples, uma linha por site:
```
Nome da Instituição | https://link-da-pagina | Categoria
```
- **Adicionar:** escreva uma nova linha.
- **Remover:** apague a linha (ou coloque `#` na frente para desativar).
- **Editar:** mude o texto da linha.

### `palavras-chave.txt` — o que conta como "obra"
Uma palavra (ou raiz) por linha. Já vem com uma lista ampla.
- Acentos não importam.
- Use a raiz: `reform` pega reforma/reformas/reformar.

Os dois arquivos são editáveis **direto na tela do GitHub**, sem instalar nada.

---

## 🚀 Instalação (uma vez só)

### 1. Conta no GitHub
Crie uma conta gratuita em [github.com](https://github.com).

### 2. Criar o repositório
- **New repository** → nome `rce-monitor-editais` → marque **Private** → **Create**.

### 3. Subir os arquivos
Faça upload de tudo deste pacote, **mantendo as pastas**:
```
monitor.py
sites.txt
palavras-chave.txt
requirements.txt
.gitignore
estado/estado.json
.github/workflows/monitor.yml
```

### 4. Criar o e-mail que envia os alertas
Recomendado: um Gmail só para o robô (ex.: `monitor.rce@gmail.com`).
1. Em [myaccount.google.com](https://myaccount.google.com) → **Segurança**
2. Ative a **Verificação em duas etapas**
3. Vá em **Senhas de app** → crie uma chamada "RCE Monitor"
4. Copie a senha de 16 caracteres gerada

### 5. Cadastrar a senha no GitHub (Secrets)
No repositório: **Settings → Secrets and variables → Actions → New repository secret**.
Crie dois:

| Nome | Valor |
|---|---|
| `GMAIL_USER` | o e-mail do robô (ex.: monitor.rce@gmail.com) |
| `GMAIL_PASS` | a senha de app de 16 caracteres |

> Os e-mails que **recebem** os alertas (você e o Nailson) já estão dentro do
> `monitor.py`. Para mudar, edite a lista `DESTINATARIOS` no topo do arquivo.

### 6. Ligar e testar
- Vá na aba **Actions** → workflow **"RCE Monitor de Editais"** → **Run workflow**.
- Veja os logs. Na primeira execução ele registra a base (sem e-mail).
- A partir daí, roda sozinho todo dia às 08h e te avisa quando houver obra nova.

---

## 🧪 Testar a lógica no seu PC (opcional)

```bash
pip install -r requirements.txt
python monitor.py --teste
```
Roda um autoteste com uma página fictícia e mostra que o filtro pega só os
editais de obra. Não acessa internet nem envia e-mail.

---

## ⚙️ Observações técnicas

- **Sites que carregam por JavaScript** (alguns portais muito dinâmicos, como o
  PNCP) podem não ser lidos por este método simples. Os sites institucionais da
  lista (Erasto, Santa Casa, SENAC, Mackenzie etc.) são lidos normalmente.
- Se um site sair do ar momentaneamente, o robô **não** dispara alerta falso —
  apenas registra a falha no log da aba Actions.
- Vale conferir a aba **Actions** de vez em quando para ver se algum site mudou
  de endereço (basta atualizar a linha no `sites.txt`).

---

Contato: comercial@rceengenharia.eng.br
