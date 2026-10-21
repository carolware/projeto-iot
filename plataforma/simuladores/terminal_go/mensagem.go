package main

// Mensagem canônica v1.0 (contratos/mensagem-canonica.v1.schema.json),
// montada só com a biblioteca padrão: integração por contrato, sem SDK.

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

const versaoContrato = "1.0"

type Mensagem struct {
	Versao      string         `json:"versao"`
	IDMensagem  string         `json:"id_mensagem"`
	Dispositivo string         `json:"dispositivo"`
	Tipo        string         `json:"tipo"`
	Timestamp   string         `json:"timestamp"`
	Dados       map[string]any `json:"dados,omitempty"`
}

// Terminal identifica um dispositivo pelo prefixo do tópico canônico:
// {inquilino}/{site}/{dominio}/{dispositivo}
type Terminal struct {
	Inquilino, Site, Dominio, Dispositivo string
}

func ParseTerminal(prefixo string) (Terminal, error) {
	partes := strings.Split(strings.Trim(prefixo, "/ "), "/")
	if len(partes) != 4 {
		return Terminal{}, fmt.Errorf("terminal %q: esperado inquilino/site/dominio/dispositivo", prefixo)
	}
	for _, p := range partes {
		if p == "" {
			return Terminal{}, fmt.Errorf("terminal %q: nível vazio", prefixo)
		}
	}
	return Terminal{partes[0], partes[1], partes[2], partes[3]}, nil
}

func (t Terminal) Topico(tipo string) string {
	return strings.Join([]string{t.Inquilino, t.Site, t.Dominio, t.Dispositivo, tipo}, "/")
}

func formatarTimestamp(momento time.Time) string {
	return momento.UTC().Format("2006-01-02T15:04:05.000Z")
}

func NovaMensagem(dispositivo, tipo string, dados map[string]any, agora time.Time) Mensagem {
	return Mensagem{
		Versao:      versaoContrato,
		IDMensagem:  uuid.NewString(),
		Dispositivo: dispositivo,
		Tipo:        tipo,
		Timestamp:   formatarTimestamp(agora),
		Dados:       dados,
	}
}

// LerComando valida o mínimo que o terminal precisa para agir com segurança.
func LerComando(payload []byte, t Terminal) (Mensagem, error) {
	var m Mensagem
	if err := json.Unmarshal(payload, &m); err != nil {
		return m, fmt.Errorf("payload não é JSON: %w", err)
	}
	switch {
	case m.Versao != versaoContrato:
		return m, fmt.Errorf("versão %q não suportada", m.Versao)
	case m.Tipo != "comando":
		return m, fmt.Errorf("tipo %q no tópico de comando", m.Tipo)
	case m.Dispositivo != t.Dispositivo:
		return m, fmt.Errorf("comando para %q chegou em %q", m.Dispositivo, t.Dispositivo)
	case m.IDMensagem == "":
		return m, errors.New("id_mensagem ausente")
	}
	if _, ok := m.Dados["acao"].(string); !ok {
		return m, errors.New("dados.acao ausente")
	}
	return m, nil
}
