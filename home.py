import io
import re
from typing import List, Dict, Optional

import streamlit as st
import pdfplumber
import pandas as pd

# ---------------------------
# Funções auxiliares (mesmas lógicas do plano.py)
# ---------------------------

MONEY_RE = re.compile(r"R\$\s*([\d\.,]+)")

def parse_money(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = MONEY_RE.search(s)
    if not m:
        s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except Exception:
            return None
    val = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(val)
    except Exception:
        return None

def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def is_tipo_pf(line: str) -> Optional[str]:
    line_up = line.strip().upper()
    if line_up in {"CONSULTA", "EXAME", "OUTROS"}:
        return line_up
    return None

RE_HEAD = re.compile(
    r"^(?P<peg>\d{6,})\s+(?P<guia>\d+)\s+(?P<data>\d{2}/\d{2}/\d{4})\s+(?P<codigo>\d\.\d{2}\.\d{2}\.\d{2}-\d)"
)

RE_TAIL = re.compile(
    r"(?P<qtd>\d+)\s+R\$\s*[\d\.,]+\s+R\$\s*[\d\.,]+$"
)

RE_VALS = re.compile(
    r"(?P<qtd>\d+)\s+R\$\s*(?P<vl_pago>[\d\.,]+)\s+R\$\s*(?P<copart>[\d\.,]+)$"
)

def extract_pdf_text(file_like) -> str:
    texts = []
    with pdfplumber.open(file_like) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    return "\n".join(texts)

def parse_text_blocks(pdf_text: str) -> pd.DataFrame:
    lines = [clean_spaces(l) for l in pdf_text.splitlines() if l.strip()]
    current_tipo_pf = None
    records: List[Dict] = []

    current_block_lines: List[str] = []
    current_head: Optional[re.Match] = None

    def flush_block():
        nonlocal current_block_lines, current_head, current_tipo_pf, records
        if not current_head or not current_block_lines:
            current_block_lines = []
            current_head = None
            return

        tail_line = None
        for l in reversed(current_block_lines):
            if RE_TAIL.search(l):
                tail_line = l
                break

        peg = current_head.group("peg")
        guia = current_head.group("guia")
        data = current_head.group("data")
        codigo = current_head.group("codigo")

        desc_lines = []
        prestador_lines = []
        qtd = None
        vl_pago = None
        copart = None

        if tail_line:
            mvals = RE_VALS.search(tail_line)
            if mvals:
                qtd = int(mvals.group("qtd"))
                vl_pago = parse_money(mvals.group("vl_pago"))
                copart = parse_money(mvals.group("copart"))

        body_lines = []
        for l in current_block_lines:
            if l == tail_line:
                continue
            if RE_HEAD.match(l):
                continue
            body_lines.append(l)

        if body_lines:
            prestador_lines.append(body_lines[-1])
            desc_lines = body_lines[:-1]
        else:
            desc_lines = []

        desc = clean_spaces(" ".join(desc_lines)) if desc_lines else None
        prestador = clean_spaces(" ".join(prestador_lines)) if prestador_lines else None

        records.append(
            {
                "tipo_pf": current_tipo_pf,
                "peg": peg,
                "guia": guia,
                "data": data,
                "codigo": codigo,
                "descricao": desc,
                "prestador": prestador,
                "qtd": qtd,
                "valor_pago": vl_pago,
                "coparticipacao": copart,
            }
        )

        current_block_lines = []
        current_head = None

    for line in lines:
        tp = is_tipo_pf(line)
        if tp:
            flush_block()
            current_tipo_pf = tp
            continue

        head = RE_HEAD.match(line)
        if head:
            flush_block()
            current_head = head
            current_block_lines = [line]
            continue

        if current_head:
            current_block_lines.append(line)
            if RE_TAIL.search(line):
                flush_block()

    flush_block()

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y", errors="coerce")
    return df

# ---------------------------
# Streamlit App
# ---------------------------

st.set_page_config(page_title="Dashboard - Demonstrativo PDF", page_icon="📄", layout="wide")

st.title("📄 Dashboard – Demonstrativo plano de saúde")
st.write("Faça o upload do PDF.")

uploaded_pdf = st.file_uploader("Carregue o PDF do demonstrativo", type=["pdf"])

if uploaded_pdf is not None:
    with st.spinner("Processando o PDF..."):
        txt = extract_pdf_text(uploaded_pdf)
        df_reg = parse_text_blocks(txt)

    if df_reg.empty:
        st.warning("Não encontrei registros estruturados no PDF.")
    else:
        # mesma regra do seu plano.py (arredondado a 0 casas decimais)
        df_reg["novo_valor"] = round((df_reg["valor_pago"] * 0.30) - df_reg["coparticipacao"], 0)
        if "data" in df_reg.columns:
            df_reg["data"] = pd.to_datetime(df_reg["data"], errors="coerce").dt.strftime("%d/%m/%Y")


        # CSVs em memória para download
        csv_all = df_reg.to_csv(index=False, encoding="utf-8-sig")
        df_neg = df_reg[df_reg["novo_valor"] < 0].copy()
        csv_neg = df_neg.to_csv(index=False, encoding="utf-8-sig")

        # KPIs
        col1, col2, col3 = st.columns(3)
        col1.metric("Registros negativos", len(df_neg))

        st.subheader("🔻 Registros com novo_valor < 0")
        st.dataframe(df_neg, use_container_width=True)

        st.download_button(
            "⬇️ Baixar pdf_registros.csv (todos)",
            data=csv_all,
            file_name="pdf_registros.csv",
            mime="text/csv"
        )

        if not df_neg.empty:
            st.download_button(
                "⬇️ Baixar pdf_registros_negativos.csv (apenas negativos)",
                data=csv_neg,
                file_name="pdf_registros_negativos.csv",
                mime="text/csv"
            )
else:
    st.info("👆 Carregue um PDF para começar.")
