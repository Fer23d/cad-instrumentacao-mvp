# InstrumentaCAD

Sistema web para analisar arquivos CAD em DXF/DWG, revisar instrumentos encontrados, gerar relatorio tecnico e devolver uma planta marcada.

## O que esta versao faz

- Recebe upload de arquivo `.dxf` e `.dwg`.
- Converte `.dwg` para `.dxf` quando o ODA File Converter esta instalado/configurado.
- Le entidades `INSERT`, `TEXT` e `MTEXT`.
- Procura instrumentos por nome de bloco, layer, texto e tags proximas.
- Classifica tipos comuns: camera, sensor, valvula, transmissores e medidores.
- Possui dashboard com historico e indicadores.
- Permite cadastrar biblioteca de simbolos por bloco/layer.
- Permite revisar e corrigir os instrumentos antes de gerar o relatorio.
- Gera relatorio profissional em PDF e Excel com dados do projeto.
- Aceita logo da empresa para a capa do PDF.
- Gera uma copia DXF marcada com os instrumentos encontrados.
- Tenta converter o DXF marcado para DWG marcado quando o ODA esta instalado.

## Como rodar

No Windows, primeiro extraia o ZIP do projeto em uma pasta, por exemplo:

```text
C:\Users\SEU_USUARIO\Downloads\cad-instrumentacao-mvp
```

Depois abra o PowerShell dentro dessa pasta e rode:

```powershell
py -m pip install -r requirements.txt
py app.py --port 8000
```

Se o comando `py` nao existir, tente:

```powershell
python -m pip install -r requirements.txt
python app.py --port 8000
```

Tambem da para clicar duas vezes no arquivo:

```text
rodar_windows.bat
```

No Linux/Codex:

```bash
cd /workspace/cad-instrumentacao-mvp
python3 app.py --port 8000
```

Depois abra:

```text
http://localhost:8000
```

## Fluxo de uso

1. Abra o dashboard.
2. Preencha nome do projeto, cliente, responsavel tecnico e tipo da planta.
3. Envie o arquivo `.dxf` ou `.dwg`.
4. Revise tags, tipos e observacoes dos instrumentos encontrados.
5. Gere PDF, Excel, DXF marcado e, quando possivel, DWG marcado.

## Arquivos gerados

Ao finalizar a revisao, o sistema gera:

| Arquivo | Uso |
|---|---|
| PDF | Relatorio tecnico com capa, dados do projeto, logo e lista de instrumentos |
| Excel | Planilha para conferencia e tratamento dos dados |
| DXF marcado | Planta com circulos, etiquetas e layer `INSTRUMENTACAD_MARCACOES` |
| DWG marcado | Copia DWG marcada, quando o ODA File Converter esta disponivel |

O arquivo original nao e alterado.

## Biblioteca de simbolos

Na tela `Biblioteca de simbolos`, cadastre padroes usados nos seus desenhos.

Exemplos:

| Bloco contem | Layer contem | Tipo |
|---|---|---|
| CAMERA | CFTV | Camera / CFTV |
| SENSOR | INSTRUMENTACAO | Sensor |
| VALV | AUTOMACAO | Valvula |
| PT | INSTRUMENTACAO | Transmissor de pressao |

## Como habilitar DWG

O formato DWG precisa de um conversor externo. Instale o ODA File Converter no computador/servidor.

Se o sistema nao encontrar automaticamente, configure o caminho no PowerShell antes de rodar o app:

```powershell
$env:ODA_FILE_CONVERTER="C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe"
py app.py --port 8000
```

Se o seu instalador usar outro caminho, ajuste o valor da variavel.

## Arquivo de teste

Use o arquivo:

```text
samples/planta_instrumentacao_exemplo.dxf
```

## Proximos passos recomendados

1. Adicionar login e contas por empresa.
2. Criar preview visual da planta no navegador.
3. Separar biblioteca de simbolos por cliente/projeto.
4. Adicionar padroes de tag por tipo de instrumento.
5. Evoluir o parser para `ezdxf` em ambiente com instalacao de pacotes.
