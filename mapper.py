#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mapper.py – botãozinhos automágicos com Playwright

O que faz:
- Abre uma URL no Edge/Chrome (Chromium)
- Espera você logar na mão
- Caça tudo que parece clicável (botão, link, submit, até dentro de iframe)
- Pinta cada um de azul pra você ir passando (N/P)
- Guarda seletores bonitinhos num JSON com nome que você escolher
- Cria várias formas de achar o elemento (CSS, XPath, role+name) e dá um "score" de confiança
- Mostra a lista com preview e score
- Testa se o seletor salvo acha o mesmo botão de novo
- Faz log de tudo (hora, URL, user, host etc.) pra auditoria

Precisa ter:
- Python 3.10+
- playwright>=1.46

Setup rápido:
    pip install playwright
    playwright install
    playwright install msedge  # se for usar Edge

Rodar:
    python mapper.py --browser edge
    python mapper.py --browser chrome

Comandos no prompt interativo:
    open <URL>     -> abre URL
    scan           -> procura os clicáveis
    walk           -> modo passo-a-passo (N/P/C/S/Q) (N = Proximo/P = Anterior/C = Captura botão/S = Pula botão/Q = Sai do modo Walk)
    list           -> lista tudo com índice, score e seletor preferido
    capture <idx>  -> salva pelo índice
    test <file>    -> carrega JSON salvo e tenta achar de novo
    reload         -> recarrega a página
    url            -> mostra URL atual
    help           -> autoexplicativo
    quit           -> sai

Cria do lado do script:
    ./JSONs  -> onde ficam os JSONs salvos
    ./logs   -> logs de auditoria

Segurança:
- O destaque visual só injeta overlay temporário via JS, não mexe no site.
- Não salva conteúdo sensível, só metadata do elemento.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
import uuid
import socket
import getpass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext, Frame, ElementHandle, TimeoutError as PWTimeoutError

# ------------------------------ Utilidades ------------------------------

ROOT = Path(__file__).resolve().parent
JSON_DIR = ROOT / "JSONs"
LOG_DIR = ROOT / "logs"
JSON_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

SESSION_ID = uuid.uuid4().hex[:8]
LOG_FILE = LOG_DIR / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{SESSION_ID}.txt"

def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ------------------------------ Seletor ------------------------------

CLICKABLE_CSS = ",".join([
    # Botões/links 
    "button",
    "[role=button]",
    "a[href]",
    "input[type=button]",
    "input[type=submit]",

    # Campos editáveis
    "input[type=text]",
    "input[type=password]",
    "input[type=search]",
    "input[type=email]",
    "input[type=tel]",
    "input[type=number]",
    "input[type=url]",
    "input:not([type])",  # sem type -> geralmente text
    "textarea",
    "select",
    "[contenteditable=''], [contenteditable='true']",
    "[role=textbox]",
    "[role=searchbox]",
    "[role=combobox]",

    # Heurísticas gerais 
    "[onclick]",
    "[tabindex]:not([tabindex='-1'])",
])

STABLE_ATTRS = [
    "data-testid", "data-test", "data-qa", "data-id", "data-cy", "data-e2e",
]

class SelectorPack(Dict[str, Any]):
    pass

# ------------------------------ Controller ------------------------------

class BrowserController:
    def __init__(self, browser_choice: str = "edge"):
        self.browser_choice = browser_choice
        self.p = None
        self.browser: Optional[Browser] = None
        self.ctx: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self):
        self.p = sync_playwright().start()
        if self.browser_choice == "edge":
            self.browser = self.p.chromium.launch(channel="msedge", headless=False)
        elif self.browser_choice == "chrome":
            self.browser = self.p.chromium.launch(channel="chrome", headless=False)
        else:
            self.browser = self.p.chromium.launch(headless=False)
        self.ctx = self.browser.new_context()
        self.page = self.ctx.new_page()
        self._log_env()
        return self

    def _log_env(self):
        uname = getpass.getuser()
        host = socket.gethostname()
        log(f"ENV user={uname} host={host} session={SESSION_ID} browser={self.browser_choice}")

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.ctx:
                self.ctx.close()
            if self.browser:
                self.browser.close()
        finally:
            if self.p:
                self.p.stop()

    def open(self, url: str):
        assert self.page is not None
        log(f"OPEN {url}")
        self.page.goto(url, wait_until="load", timeout=120_000)

    def current_url(self) -> str:
        return self.page.url if self.page else ""

# ------------------------------ Coleta ------------------------------

class ElementRecord:
    def __init__(self, frame_path: List[str], handle: ElementHandle, index_in_scan: int):
        self.frame_path = frame_path
        self.handle = handle
        self.index = index_in_scan
        self.meta: Dict[str, Any] = {}

    def enrich(self, page_like: Page | Frame):
        el = self.handle
        try:
            el.scroll_into_view_if_needed(timeout=5_000)
        except PWTimeoutError:
            pass

        bbox = el.bounding_box() or {"x": None, "y": None, "width": None, "height": None}
        tag = el.evaluate("e=>e.tagName.toLowerCase()")
        id_ = el.get_attribute("id")
        cls = el.get_attribute("class")
        role_attr = el.get_attribute("role")
        aria_label = el.get_attribute("aria-label")
        text = el.evaluate("e=> (e.innerText||'').trim().slice(0,200)")
        title = el.get_attribute("title")
        href = el.get_attribute("href")
        type_ = el.get_attribute("type")

        # Campos relevantes pra inputs/textarea/select/contenteditable
        placeholder = el.get_attribute("placeholder")
        name_attr   = el.get_attribute("name")
        autocomplete = el.get_attribute("autocomplete")
        readonly = el.get_attribute("readonly") is not None
        required = el.get_attribute("required") is not None
        aria_disabled = (el.get_attribute("aria-disabled") == "true")
        inputmode = el.get_attribute("inputmode")
        maxlength = el.get_attribute("maxlength")
        contenteditable_attr = el.get_attribute("contenteditable")
        is_contenteditable = (contenteditable_attr == "" or contenteditable_attr == "true")

        # NÃO gravar valor real por segurança/compliance; só comprimento (0 se não aplicável)
        try:
            value_length = el.evaluate("e=> (e.value !== undefined ? String(e.value).length : 0)")
        except Exception:
            value_length = 0

        # Atributos estáveis
        attrs = {}
        for a in STABLE_ATTRS:
            val = el.get_attribute(a)
            if val:
                attrs[a] = val

        css = generate_css_selector(page_like, el)
        xpath = generate_xpath(page_like, el)

        # Inferência de role/name com conhecimento de input/textarea/select
        role_name = infer_role_name(tag, role_attr, aria_label, text, type_)

        # Se o nome não veio de aria-label/texto, tenta placeholder
        if not role_name.get("name") and placeholder:
            role_name["name"] = placeholder

        # Classificação do campo (pra listagem e JSON)
        if is_contenteditable:
            field_kind = "contenteditable"
        elif tag == "textarea":
            field_kind = "textarea"
        elif tag == "select" or role_name.get("role") == "combobox":
            field_kind = "select"
        elif tag == "input":
            t = (type_ or "text").lower()
            if t in {"text","password","search","email","tel","number","url"}:
                field_kind = f"input:{t}"
            elif t in {"button","submit"}:
                field_kind = "input:button"
            else:
                field_kind = f"input:{t}"
        else:
            field_kind = "other"

        score = score_selector(id_, attrs, role_name, tag)

        self.meta = {
            "frame_path": self.frame_path,
            "index": self.index,
            "tag": tag,
            "id": id_,
            "classes": cls,
            "role": role_name.get("role"),
            "name": role_name.get("name"),
            "type": type_,
            "title": title,
            "text": text,
            "href": href,
            "stable_attrs": attrs,
            "selectors": {
                "css": css,
                "xpath": xpath,
                "role_name": role_name,
            },
            "bbox": bbox,
            "score": score,
            # NOVO bloco com detalhes de campo editável
            "field": {
                "kind": field_kind,
                "placeholder": placeholder,
                "name": name_attr,
                "autocomplete": autocomplete,
                "readonly": readonly,
                "required": required,
                "aria_disabled": aria_disabled,
                "inputmode": inputmode,
                "maxlength": maxlength,
                "contenteditable": is_contenteditable,
                "value_length": value_length
            },
            # Aria extra útil de debugging
            "aria": {
                "label": aria_label,
                "labelledby": el.get_attribute("aria-labelledby"),
                "describedby": el.get_attribute("aria-describedby"),
            },
        }
        return self


def infer_role_name(tag: str, role: Optional[str], aria_label: Optional[str], text: str, type_: Optional[str]) -> Dict[str, Optional[str]]:
    # Se já veio role explícito, respeita
    r = role
    if not r:
        if tag == "input":
            if type_ in (None, "text", "email", "tel", "url", "password", "number"):
                r = "textbox"
            elif type_ == "search":
                r = "searchbox"
            elif type_ in ("button", "submit"):
                r = "button"
        elif tag == "textarea":
            r = "textbox"
        elif tag == "select":
            r = "combobox"
        elif tag == "a":
            r = "link"
        elif tag == "button":
            r = "button"

    # Nome: prioriza aria-label, depois placeholder, depois texto
    # Aqui só decide entre aria e texto; placeholder entra no enrich e volta pelo dict
    name = aria_label or (text if text else None)
    return {"role": r, "name": name}


def score_selector(id_: Optional[str], stable_attrs: Dict[str, str], role_name: Dict[str, Optional[str]], tag: str) -> int:
    score = 0
    if role_name.get("role") and role_name.get("name"):
        score += 40
    if id_ and re.match(r"^[A-Za-z_][A-Za-z0-9_\-:.]{2,}$", id_):
        score += 40
    score += min(len(stable_attrs) * 10, 30)
    if tag in {"button", "a", "input"}:
        score += 10
    return score


def walk_frames(root: Page | Frame, path: Optional[List[str]] = None) -> List[Tuple[List[str], Frame]]:
    path = path or []
    frames: List[Tuple[List[str], Frame]] = []
    for fr in root.frames if isinstance(root, Page) else root.child_frames:
        name = fr.name or fr.url or "<anonymous>"
        frames.append((path + [name], fr))
        frames.extend(walk_frames(fr, path + [name]))
    return frames


def collect_clickables(page: Page) -> List[ElementRecord]:
    records: List[ElementRecord] = []
    index = 0

    def collect_in(container: Page | Frame, frame_path: List[str]):
        nonlocal index
        handles = container.query_selector_all(CLICKABLE_CSS)
        # Filtro JS para elementos realmente clicáveis e visíveis
        filtered: List[ElementHandle] = []
        for h in handles:
            try:
                visible = h.is_visible()
            except Exception:
                visible = False
            if not visible:
                continue
            disabled = h.evaluate("e => e.disabled === true || e.hasAttribute('disabled') || e.getAttribute('aria-disabled') === 'true'")
            if disabled:
                continue
            filtered.append(h)
        for h in filtered:
            rec = ElementRecord(frame_path, h, index)
            rec.enrich(container)
            records.append(rec)
            index += 1

    collect_in(page, [])
    # Todos os frames descendentes
    for path, fr in walk_frames(page):
        try:
            collect_in(fr, path)
        except Exception as e:
            log(f"WARN frame_collect path_len={len(path)} err={e}")
    # Ordena por Y, depois X para passeio intuitivo
    records.sort(key=lambda r: (r.meta.get("bbox", {}).get("y") or 0, r.meta.get("bbox", {}).get("x") or 0))
    return records

# ------------------------------ Geradores: Make by GPT ------------------------------

JS_CSS_SELECTOR = """
(e)=>{
  function cssPath(el){
    if (!(el instanceof Element)) return '';
    const path=[];
    while (el && el.nodeType===Node.ELEMENT_NODE){
      let selector = el.nodeName.toLowerCase();
      if (el.id){ selector += '#' + CSS.escape(el.id); path.unshift(selector); break; }
      else {
        let sib = el, nth=1;
        while (sib = sib.previousElementSibling){ if (sib.nodeName.toLowerCase()===selector) nth++; }
        if (nth!==1){ selector += `:nth-of-type(${nth})`; }
      }
      path.unshift(selector);
      el = el.parentElement;
    }
    return path.join('>');
  }
  return cssPath(e);
}
"""

JS_XPATH = """
(e)=>{
  function xpath(el){
    if (el && el.nodeType===Node.ELEMENT_NODE){
      const parts=[];
      while (el && el.nodeType===Node.ELEMENT_NODE){
        let ix = 0;
        let sib = el.previousSibling;
        while(sib){ if (sib.nodeType===Node.ELEMENT_NODE && sib.nodeName===el.nodeName) ix++; sib = sib.previousSibling; }
        const tag = el.nodeName.toLowerCase();
        const seg = `${tag}[${ix+1}]`;
        parts.unshift(seg);
        el = el.parentNode;
      }
      return '/' + parts.join('/');
    }
    return '';
  }
  return xpath(e);
}
"""

def generate_css_selector(node_context: Page | Frame, el: ElementHandle) -> str:
    try:
        return el.evaluate(JS_CSS_SELECTOR)
    except Exception:
        return ""


def generate_xpath(node_context: Page | Frame, el: ElementHandle) -> str:
    try:
        return el.evaluate(JS_XPATH)
    except Exception:
        return ""

# ------------------------------ Highlighter ------------------------------

HIGHLIGHT_JS = """
(target)=>{
  const prev = document.getElementById('__rpa_mapper_overlay__');
  if (prev) prev.remove();
  const rect = target.getBoundingClientRect();
  const o = document.createElement('div');
  o.id='__rpa_mapper_overlay__';
  o.style.position='fixed';
  o.style.pointerEvents='none';
  o.style.zIndex='2147483647';
  o.style.left = (rect.left - 2) + 'px';
  o.style.top = (rect.top - 2) + 'px';
  o.style.width = (rect.width + 4) + 'px';
  o.style.height = (rect.height + 4) + 'px';
  o.style.border='2px solid #1e90ff';
  o.style.borderRadius='4px';
  o.style.boxShadow='0 0 8px rgba(30,144,255,0.7)';
  document.body.appendChild(o);
}
"""

REMOVE_HIGHLIGHT_JS = """
()=>{ const prev=document.getElementById('__rpa_mapper_overlay__'); if(prev) prev.remove(); }
"""

class Highlighter:
    def __init__(self, controller: BrowserController):
        self.ctrl = controller

    def show(self, rec: ElementRecord):
        # Navega para o frame correto e injeta overlay
        page = self.ctrl.page
        if not page:
            return
        target_frame = page
        # Resolve frame path
        for path_node in rec.frame_path:
            # Busca pelo nome/url
            found = None
            for fr in (target_frame.frames if isinstance(target_frame, Page) else target_frame.child_frames):
                if fr.name == path_node or fr.url == path_node:
                    found = fr
                    break
            if not found:
                # fallback: primeiro filho
                if isinstance(target_frame, Page) and target_frame.frames:
                    found = target_frame.frames[0]
                elif hasattr(target_frame, 'child_frames') and target_frame.child_frames:
                    found = target_frame.child_frames[0]
            target_frame = found or target_frame
        try:
            rec.handle.scroll_into_view_if_needed(timeout=5_000)
            rec.handle.evaluate(HIGHLIGHT_JS)
        except Exception as e:
            log(f"HIGHLIGHT error idx={rec.index} err={e}")

    def clear(self):
        page = self.ctrl.page
        if page:
            try:
                page.evaluate(REMOVE_HIGHLIGHT_JS)
            except Exception:
                pass

# ------------------------------ Storage ------------------------------

class Storage:
    def __init__(self, base: Path):
        self.base = base

    def _dedup_name(self, name: str) -> Path:
        sanitized = re.sub(r"[^A-Za-z0-9_\-]", "_", name)
        candidate = self.base / f"{sanitized}.json"
        k = 2
        while candidate.exists():
            candidate = self.base / f"{sanitized}({k}).json"
            k += 1
        return candidate

    def save_record(self, url: str, rec: ElementRecord, custom_name: str):
        path = self._dedup_name(custom_name)
        payload = {
            "saved_at": datetime.now().isoformat(),
            "page_url": url,
            "machine_user": getpass.getuser(),
            "machine_host": socket.gethostname(),
            "session": SESSION_ID,
            "element": rec.meta,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        log(f"SAVED {path.name} idx={rec.index} url={url}")
        return path

    def load_record(self, filename: str) -> Dict[str, Any]:
        path = self.base / filename
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

# ------------------------------ CLI principal ------------------------------

class RPAMapper:
    def __init__(self, browser_choice: str):
        self.browser_choice = browser_choice
        self.ctrl = BrowserController(browser_choice)
        self.highlighter = None
        self.records: List[ElementRecord] = []
        self.storage = Storage(JSON_DIR)

    def run(self):
        log("SESSION START")
        with self.ctrl as ctrl:
            self.highlighter = Highlighter(ctrl)
            self.repl()
        log("SESSION END")

    # -------------------------- Comandos --------------------------
    def repl(self):
        while True:
            try:
                cmd = input("rpa> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                cmd = "quit"
            if not cmd:
                continue
            parts = cmd.split()
            op = parts[0].lower()

            if op == "open" and len(parts) >= 2:
                url = parts[1]
                try:
                    self.ctrl.open(url)
                except Exception as e:
                    log(f"ERROR open url={url} err={e}")

            elif op == "url":
                print(self.ctrl.current_url())

            elif op == "scan":
                if not self.ctrl.page:
                    log("WARN no page")
                    continue
                self.records = collect_clickables(self.ctrl.page)
                log(f"SCAN found={len(self.records)}")

            elif op == "list":
                if not self.records:
                    log("No records. Use 'scan' first.")
                    continue
                for r in self.records:
                    sel = r.meta.get("selectors", {}).get("css") or r.meta.get("selectors", {}).get("xpath")
                    name = r.meta.get("name") or (r.meta.get("text")[:40] if r.meta.get("text") else "")
                    kind = (r.meta.get("field") or {}).get("kind")
                    print(f"[{r.index:03d}] score={r.meta.get('score',0):3d} tag={r.meta.get('tag')} role={r.meta.get('role')} kind={kind} name={name!r} sel={sel[:80] if sel else ''}")

            elif op == "walk":
                self.walk_mode()

            elif op == "capture" and len(parts) >= 2:
                try:
                    idx = int(parts[1])
                except ValueError:
                    log("capture <index>")
                    continue
                self.capture_by_index(idx)

            elif op == "test" and len(parts) >= 2:
                self.test_saved(parts[1])

            elif op == "reload":
                try:
                    self.ctrl.page.reload()
                except Exception as e:
                    log(f"ERROR reload err={e}")

            elif op == "help":
                print(__doc__)

            elif op == "quit":
                break

            else:
                log("Unknown command. Type 'help'.")

    def walk_mode(self):
        if not self.records:
            log("No records. Use 'scan' first.")
            return
        i = 0
        max_i = len(self.records) - 1
        log("WALK mode: N=next, P=prev, C=capture, S=skip, Q=quit")
        while True:
            rec = self.records[i]
            self.highlighter.show(rec)
            print(f"idx={rec.index} tag={rec.meta.get('tag')} role={rec.meta.get('role')} text={rec.meta.get('text')!r} score={rec.meta.get('score')}")
            try:
                k = input("[N/P/C/S/Q]> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                k = 'q'
            if k == 'n':
                i = min(i + 1, max_i)
            elif k == 'p':
                i = max(i - 1, 0)
            elif k == 'c':
                self.capture_record(rec)
            elif k == 's':
                i = min(i + 1, max_i)
            elif k == 'q':
                self.highlighter.clear()
                break

    def capture_record(self, rec: ElementRecord):
        name = input("Nome para o JSON (ex: botao_login): ").strip()
        if not name:
            log("Capture cancelada: nome vazio.")
            return
        url = self.ctrl.current_url()
        path = self.storage.save_record(url, rec, name)
        print(f"Salvo: {path}")

    def capture_by_index(self, idx: int):
        matches = [r for r in self.records if r.index == idx]
        if not matches:
            log(f"Índice {idx} não encontrado. Faça 'scan' e use 'list'.")
            return
        self.capture_record(matches[0])

    def test_saved(self, filename: str):
        try:
            data = self.storage.load_record(filename)
        except FileNotFoundError:
            log(f"Arquivo não encontrado: {filename}")
            return

        page = self.ctrl.page
        if not page:
            log("Sem página aberta")
            return

        # Alerta de URL diferente (não navega automaticamente)
        saved_url = data.get("page_url")
        cur = page.url
        if saved_url and saved_url.split('?')[0] != cur.split('?')[0]:
            log(f"WARN URL atual difere da salva (saved={saved_url} current={cur})")

        # Metadados salvos
        elem = data.get("element", {}) or {}
        sel = elem.get("selectors", {}) or {}
        css = sel.get("css")
        xpath = sel.get("xpath")
        role = (sel.get("role_name") or {}).get("role")
        name = (sel.get("role_name") or {}).get("name")
        id_ = elem.get("id")
        stable = elem.get("stable_attrs") or {}

        # Resolve frame alvo primeiro
        target: Page | Frame = page
        frame_path = elem.get("frame_path") or []
        for node in frame_path:
            found = None
            for fr in (target.frames if isinstance(target, Page) else target.child_frames):
                if fr.name == node or fr.url == node:
                    found = fr
                    break
            target = found or target

        # 1) Tentativa mais fiel: role + name (Playwright)
        if role and name:
            try:
                handle = target.get_by_role(role, name=name).first.element_handle()
                if handle:
                    rec = ElementRecord(frame_path, handle, -1).enrich(target)
                    self.highlighter.show(rec)
                    log("TEST ok: destaque aplicado via role+name.")
                    return
            except Exception:
                pass

        # 2) Fallbacks em ordem de robustez
        name_attr = (elem.get("field") or {}).get("name")
        placeholder = (elem.get("field") or {}).get("placeholder")

        search_order: List[Tuple[str, str]] = []
        if role:
            search_order.append((f"[role='{css_escape(role)}']", "css"))
        if id_:
            search_order.append((f"#{css_escape(id_)}", "css"))
        for k, v in stable.items():
            search_order.append((f"[{k}='{css_escape(v)}']", "css"))
        if name_attr:
            search_order.append((f"[name='{css_escape(name_attr)}']", "css"))
        if placeholder:
            search_order.append((f"[placeholder='{css_escape(placeholder)}']", "css"))
        if css:
            search_order.append((css, "css"))
        if xpath:
            search_order.append((xpath, "xpath"))

        handle = None
        for query, kind in search_order:
            try:
                if kind == "css":
                    handle = target.query_selector(query)
                else:
                    handle = target.locator(f"xpath={query}").element_handle()
                if handle:
                    break
            except Exception:
                continue

        if handle is None:
            log("Elemento não localizado pelos seletores salvos.")
            return

        rec = ElementRecord(frame_path, handle, -1).enrich(target)
        self.highlighter.show(rec)
        log("TEST ok: destaque aplicado.")

# ------------------------------ Socorro Deus da vida ------------------------------

def css_escape(s: str) -> str:
    return re.sub(r"([^A-Za-z0-9_-])", lambda m: f"\\{m.group(1)}", s)

# ------------------------------ main ------------------------------

def main():
    parser = argparse.ArgumentParser(description="Edge/Chrome RPA Button Mapper – Python")
    parser.add_argument("--browser", choices=["edge", "chrome", "chromium"], default="edge", help="Navegador (canal) a usar")
    args = parser.parse_args()

    log("STARTUP")
    log(f"Args: {vars(args)}")

    mapper = RPAMapper(browser_choice=args.browser)
    try:
        mapper.run()
    except Exception as e:
        log(f"FATAL {e}")
        raise

if __name__ == "__main__":
    main()
