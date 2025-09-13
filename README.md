# RPAMapper – Mapeador de elementos clicáveis e campos editáveis para automação

Documento técnico de referência.
Artefato: `mapper.py` (Python 3.10+ / Playwright ≥ 1.46)

---

## Sumário

1. [Visão geral](#visão-geral)
2. [Arquitetura e componentes](#arquitetura-e-componentes)
3. [Requisitos e compatibilidade](#requisitos-e-compatibilidade)
4. [Instalação](#instalação)
5. [Execução](#execução)
6. [Fluxo operacional](#fluxo-operacional)
7. [Comandos do REPL](#comandos-do-repl)
8. [Coleta e qualificação de elementos](#coleta-e-qualificação-de-elementos)
9. [Formato de saída (JSON)](#formato-de-saída-json)
10. [Logs](#logs)
11. [Segurança e conformidade](#segurança-e-conformidade)
12. [Boas práticas de seletores](#boas-práticas-de-seletores)
13. [Erros comuns e troubleshooting](#erros-comuns-e-troubleshooting)
14. [Limitações](#limitações)
15. [Anexos](#anexos)

---

## Visão geral

**RPAMapper** é um utilitário interativo para **descobrir, revisar e persistir seletores** de:

* **Elementos clicáveis** (botões, links e afins)
* **Campos editáveis** (inputs de texto/senha/etc., `textarea`, `select`, regiões `contenteditable`)

Recursos-chave:

* Login **manual** no browser (nenhuma credencial é manipulada pelo script)
* Varredura em página **e iframes**
* Destaque visual com overlay temporário
* Seletores **CSS, XPath** e heurística **role+name**
* **Score** de robustez do alvo
* Persistência em **JSON** nominal
* Revalidação (**test**) priorizando **role+name** e, na falta, `id`, `data-*`, `[name]`, `[placeholder]`, CSS, XPath
* **Logs** de sessão para rastreabilidade

---

## Arquitetura e componentes

* **`BrowserController`**: lifecycle do Playwright (browser/context/page) e log de ambiente.
* **`collect_clickables`**: varredura e filtro de elementos elegíveis (visíveis, não-desabilitados), incluindo iframes.
* **`ElementRecord`**: coleta metadados, **classifica o tipo de campo** (kind) e gera seletores; calcula **bbox** e **score**.
* **`Highlighter`**: overlay temporário no **contexto do próprio frame** do elemento.
* **`Storage`**: salvamento/leitura de JSON com deduplicação de nomes.
* **`RPAMapper`**: REPL (console) com `open`, `scan`, `list`, `walk`, `capture`, `test`, etc.

**Diretórios gerados**

* `./JSONs/` – arquivos de elementos (um por alvo)
* `./logs/` – trilha de auditoria por sessão

---

## Requisitos e compatibilidade

* **Python** ≥ 3.10
* **Playwright** ≥ 1.46
* Navegadores: **Edge** (`msedge`), **Chrome** (`chrome`) ou **Chromium** (fallback)
* Execução **não-headless** (para login e inspeção visual)

---

## Instalação

```bash
pip install playwright
playwright install
# Se usar Edge:
playwright install msedge
```

Ambientes corporativos: validar proxy/políticas de download de binários e executar em estação/VDI de automação conforme governança.

---

## Execução

```bash
python mapper.py --browser edge
# ou
python mapper.py --browser chrome
# ou
python mapper.py --browser chromium
```

`--browser` (default `edge`): escolhe o canal do navegador.

---

## Fluxo operacional

1. **Abrir URL** e fazer **login manual**
   `open <URL>`
2. **Escanear** elementos/inputs (após a página “assentar”)
   `scan`
3. **Listar** candidatos com score/preview
   `list`
4. **Inspecionar** passo a passo com destaque
   `walk` → `N`/`P` navega, `C` captura, `S` pula, `Q` sai
5. **Capturar** por índice ou no walk
   `capture <idx>` ou tecla `C`
6. **Revalidar** um JSON salvo
   `test <arquivo.json>`

---

## Comandos do REPL

* `open <URL>` – abre URL (timeout 120s)
* `scan` – varre página + iframes; filtra visibilidade e desabilitado
* `list` – exibe `[idx] score tag role kind name sel`
* `walk` – modo passo-a-passo (N/P/C/S/Q)
* `capture <idx>` – salva o elemento pelo índice
* `test <file>` – revalida seletores e aplica highlight
* `reload` – recarrega a página
* `url` – mostra URL atual
* `help` – ajuda embutida
* `quit` – encerra

---

## Coleta e qualificação de elementos

### Critérios de elegibilidade (CSS combinado)

```css
button,
[role=button],
a[href],
input[type=button],
input[type=submit],

/* Campos editáveis */
input[type=text],
input[type=password],
input[type=search],
input[type=email],
input[type=tel],
input[type=number],
input[type=url],
input:not([type]),
textarea,
select,
[contenteditable=''], [contenteditable='true'],
[role=textbox],
[role=searchbox],
[role=combobox],

/* Heurísticas gerais */
[onclick],
[tabindex]:not([tabindex='-1'])
```

### Filtros aplicados

* **Visibilidade** verdadeira no momento do scan
* **Não-desabilitado** por:
  `e.disabled === true`, **ou** `hasAttribute('disabled')`, **ou** `aria-disabled === 'true'` → **descartado**

### Metadados armazenados

* Básico: `tag`, `id`, `classes`, `type`, `title`, `href`
* Acessibilidade: **`role`** e **`name`** (prioriza `aria-label`; fallback para texto ou `placeholder`)
* **Texto interno** (até **200** caracteres)
* **Atributos estáveis**: `data-testid`, `data-test`, `data-qa`, `data-id`, `data-cy`, `data-e2e`
* **Seletores**: `css`, `xpath`, `role_name`
* **Bounding box**: `x`, `y`, `width`, `height`
* **Trilha de frames**: `frame_path` (localização dentro de iframes)
* **Campo “field”** (para editáveis):

  * `kind` (ex.: `input:text`, `input:password`, `textarea`, `select`, `contenteditable`)
  * `placeholder`, `name`, `autocomplete`, `readonly`, `required`, `aria_disabled`, `inputmode`, `maxlength`, `contenteditable`
  * **`value_length`** (não grava o valor; apenas o comprimento)
* **Aria extra**: `label`, `labelledby`, `describedby`

### Score de robustez (0–120)

* `role` **e** `name`: **+40**
* `id` com formato estável: **+40**
* `data-*` estáveis: **+10** cada (até **+30**)
* Tag típica (`button`, `a`, `input`): **+10**

---

## Formato de saída (JSON)

Arquivo por captura em `./JSONs`. Exemplo abreviado de um **input\:text**:

```json
{
  "saved_at": "2025-09-13T15:20:00Z",
  "page_url": "https://example.com/demo",
  "machine_user": "user",
  "machine_host": "HOST",
  "session": "deadbeef",
  "element": {
    "frame_path": [],
    "index": 0,
    "tag": "input",
    "id": "username",
    "classes": "form-control",
    "role": "textbox",
    "name": "Usuário",
    "type": "text",
    "title": null,
    "text": "",
    "href": null,
    "stable_attrs": {
      "data-testid": "login-user"
    },
    "selectors": {
      "css": "form > input[name='username']",
      "xpath": "/html/body/form[1]/input[1]",
      "role_name": {
        "role": "textbox",
        "name": "Usuário"
      }
    },
    "bbox": { "x": 420, "y": 310, "width": 260, "height": 32 },
    "score": 90,
    "field": {
      "kind": "input:text",
      "placeholder": "Usuário",
      "name": "username",
      "autocomplete": "username",
      "readonly": false,
      "required": true,
      "aria_disabled": false,
      "inputmode": null,
      "maxlength": "64",
      "contenteditable": false,
      "value_length": 0
    },
    "aria": {
      "label": null,
      "labelledby": "lblUser",
      "describedby": null
    }
  }
}
```

> O nome do arquivo é o informado no prompt; se já existir, versiona com `(2)`, `(3)`, etc.

---

## Logs

* Um arquivo por sessão em `./logs/session_YYYYMMDD_HHMMSS_<id>.txt`
* Itens: timestamp, evento, usuário, host, sessão, navegador, URL relevante
* Eventos típicos: `STARTUP`, `ENV`, `OPEN`, `SCAN`, `SAVED`, `TEST ok`, `WARN ...`, `ERROR ...`, `FATAL ...`

**Retenção**: alinhar a LGPD/BCB/ISO 27001 (ex.: 90 dias) e considerar consolidação em SIEM.

---

## Segurança e conformidade

* **Credenciais**: nunca coletadas; login é manual.
* **Overlay**: apenas visual, temporário, no contexto do frame do elemento; não altera estado do site.
* **Privacidade**: não grava valor de inputs; apenas `value_length`.
* **Conteúdo**: `text` do elemento é truncado em **200** caracteres. Evitar capturar controles com conteúdo sensível no texto.
* **Rastreabilidade**: logs incluem usuário/host/sessão/URL.

---

## Boas práticas de seletores

1. Priorize **`data-testid`/`data-qa`/`data-cy`** definidos pelo front.
2. Use **role+name** quando a acessibilidade estiver correta.
3. Evite **XPath absoluto**; prefira CSS ou atributos estáveis.
4. Desconfie de **IDs voláteis**.
5. Documente e respeite o **`frame_path`** em apps com iframes.
6. Revalide após deploys: `test <arquivo.json>` como smoke test.

---

## Erros comuns e troubleshooting

| Sintoma                                     | Causa provável                                      | Ação recomendada                                                                                           |
| ------------------------------------------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `WARN frame_collect ... Frame was detached` | Iframe recarregou/trocou durante o scan             | Repetir `scan` após estabilizar a página; considerar `wait_for_load_state('networkidle')` antes do `scan`. |
| `SCAN found=0`                              | DOM ainda não disponível / atrás de login ou modal  | Fazer login, fechar modais, `reload`, então `scan`.                                                        |
| Test não destaca                            | Seletores antigos, `role` genérico, frame diferente | Conferir `frame_path`, preferir `data-*`, reexecutar `scan/capture`.                                       |
| Overlay ficou na tela                       | Saiu do `walk` sem `Q` ou overlay em iframe         | Rodar `walk` e `Q`; novo destaque substitui o anterior; `clear()` remove em main frame e iframes.          |
| Muitos candidatos                           | Página com muitos `role=button` genéricos           | Use `list` e filtre por `kind`/`name`; capture apenas os necessários.                                      |

---

## Limitações

* **Shadow DOM**: não varrido na versão atual.
* **Conteúdo altamente dinâmico**: pode exigir interação manual antes do `scan`.
* **Múltiplas abas/janelas**: a sessão trabalha com **uma** página ativa.

---

## Anexos

### A. Checklist de operação

* [ ] Ambiente/VDI de automação com hardening
* [ ] Binários do Playwright versionados/homologados
* [ ] Política de retenção de logs aplicada
* [ ] Convenções de `data-testid`/`data-qa` acordadas com o front-end
* [ ] Processo de revalidação pós-deploy

### B. Exemplos rápidos

**Abrir, escanear e listar**

```
rpa> open https://exemplo.banco.com
rpa> scan
rpa> list
```

**Inspecionar e capturar**

```
rpa> walk
[N/P/C/S/Q]> n
[N/P/C/S/Q]> c
Nome para o JSON (ex: botao_login): botao_login
```

**Revalidar**

```
rpa> test botao_login.json
```

---

**Status**: pronto para uso por times de QA/RPA com governança de logs e preferência por atributos estáveis no front-end.
