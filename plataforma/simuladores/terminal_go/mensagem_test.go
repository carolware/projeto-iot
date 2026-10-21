package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"testing"
	"time"
)

var terminal = Terminal{"pref-anapolis", "defesa-civil", "alerta_publico", "terminal-dc-01"}

func TestParseTerminal(t *testing.T) {
	got, err := ParseTerminal(" pref-anapolis/defesa-civil/alerta_publico/terminal-dc-01 ")
	if err != nil || got != terminal {
		t.Fatalf("ParseTerminal = %+v, %v", got, err)
	}
	if got.Topico("comando") != "pref-anapolis/defesa-civil/alerta_publico/terminal-dc-01/comando" {
		t.Fatalf("tópico errado: %s", got.Topico("comando"))
	}
	for _, ruim := range []string{"a/b/c", "a//c/d", ""} {
		if _, err := ParseTerminal(ruim); err == nil {
			t.Errorf("ParseTerminal(%q) deveria falhar", ruim)
		}
	}
}

func TestNovaMensagemSegueOContrato(t *testing.T) {
	agora := time.Date(2026, 10, 19, 2, 10, 4, 123_456_789, time.FixedZone("BRT", -3*3600))
	m := NovaMensagem("terminal-dc-01", "evento", map[string]any{"evento": "recebido"}, agora)
	if m.Timestamp != "2026-10-19T05:10:04.123Z" {
		t.Errorf("timestamp não está em UTC com milissegundos: %s", m.Timestamp)
	}
	if !regexp.MustCompile(`^[0-9a-f-]{36}$`).MatchString(m.IDMensagem) || m.Versao != "1.0" {
		t.Errorf("mensagem fora do contrato: %+v", m)
	}
	outra := NovaMensagem("terminal-dc-01", "evento", nil, agora)
	if outra.IDMensagem == m.IDMensagem {
		t.Error("id_mensagem repetido")
	}
	bruto, _ := json.Marshal(outra)
	var campos map[string]any
	_ = json.Unmarshal(bruto, &campos)
	if _, tem := campos["dados"]; tem {
		t.Error("dados vazio não deveria ser serializado")
	}
}

// O exemplo publicado em contratos/ é exatamente o que o terminal recebe.
func TestLerComandoDoExemploPublicado(t *testing.T) {
	caminho := filepath.Join("..", "..", "contratos", "exemplos", "comando-terminal.json")
	payload, err := os.ReadFile(caminho)
	if err != nil {
		t.Skipf("exemplo indisponível (%v)", err)
	}
	m, err := LerComando(payload, terminal)
	if err != nil {
		t.Fatalf("exemplo do contrato recusado: %v", err)
	}
	if m.Dados["acao"] != "exibir_alerta" {
		t.Errorf("acao = %v", m.Dados["acao"])
	}
}

func TestLerComandoRecusaForaDoContrato(t *testing.T) {
	casos := map[string]string{
		"nao json":       `{`,
		"versao":         `{"versao":"2.0","id_mensagem":"x","dispositivo":"terminal-dc-01","tipo":"comando","dados":{"acao":"a"}}`,
		"tipo":           `{"versao":"1.0","id_mensagem":"x","dispositivo":"terminal-dc-01","tipo":"evento","dados":{"acao":"a"}}`,
		"outro terminal": `{"versao":"1.0","id_mensagem":"x","dispositivo":"terminal-bm-01","tipo":"comando","dados":{"acao":"a"}}`,
		"sem id":         `{"versao":"1.0","dispositivo":"terminal-dc-01","tipo":"comando","dados":{"acao":"a"}}`,
		"sem acao":       `{"versao":"1.0","id_mensagem":"x","dispositivo":"terminal-dc-01","tipo":"comando","dados":{}}`,
	}
	for nome, payload := range casos {
		if _, err := LerComando([]byte(payload), terminal); err == nil {
			t.Errorf("%s: deveria ser recusado", nome)
		}
	}
}

// Mesmo vetor calculado por nucleo/credenciais.py (Python): as duas linguagens
// precisam chegar à mesma credencial, senão o broker recusa o terminal.
func TestDerivarSenhaIgualAoPython(t *testing.T) {
	if got := DerivarSenha("segredo-de-teste", "terminal-dc-anapolis"); got != "D6OIyrhcguk2E995MGA6YxgO7vQkQIGU" {
		t.Fatalf("DerivarSenha = %s", got)
	}
}
