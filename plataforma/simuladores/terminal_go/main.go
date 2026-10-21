// Terminal de alerta urbano (prefeitura, Defesa Civil, bombeiros) — simulado.
//
// Recebe os alertas que a plataforma encaminha às prefeituras e os repassa
// ao operador. Escrito em Go para demonstrar a integração em uma 3ª linguagem
// usando só MQTT genérico e o contrato publicado (RF-INT-02).
//
//	assina   {inquilino}/{site}/alerta_publico/{terminal}/comando
//	publica  .../evento  {"evento": "recebido" | "reconhecido", "comando_id": ...}
//	         .../estado  {"online": true, "alerta_pendente": ..., "ultimo_alarme_id": ...}
//
// Variáveis: MQTT_BROKER, MQTT_PORT, TERMINAIS (lista separada por vírgula de
// inquilino/site/dominio/dispositivo), ESTADO_INTERVALO_S, AUTO_RECONHECER_S.
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

type estadoTerminal struct {
	alertaPendente bool
	ultimoAlarmeID any
}

type Simulador struct {
	cliente         mqtt.Client
	terminais       map[string]Terminal // chave: tópico de comando
	estados         map[string]*estadoTerminal
	mu              sync.Mutex
	autoReconhecer  time.Duration
	intervaloEstado time.Duration
}

func env(nome, padrao string) string {
	if v := os.Getenv(nome); v != "" {
		return v
	}
	return padrao
}

func envSegundos(nome string, padrao float64) time.Duration {
	v, err := strconv.ParseFloat(env(nome, fmt.Sprint(padrao)), 64)
	if err != nil {
		log.Fatalf("%s inválido: %v", nome, err)
	}
	return time.Duration(v * float64(time.Second))
}

func (s *Simulador) publicar(t Terminal, tipo string, dados map[string]any) {
	m := NovaMensagem(t.Dispositivo, tipo, dados, time.Now())
	payload, _ := json.Marshal(m)
	s.cliente.Publish(t.Topico(tipo), 1, false, payload)
}

func (s *Simulador) aoReceberComando(_ mqtt.Client, msg mqtt.Message) {
	t, ok := s.terminais[msg.Topic()]
	if !ok {
		return
	}
	comando, err := LerComando(msg.Payload(), t)
	if err != nil {
		log.Printf("[%s] comando recusado: %v", t.Dispositivo, err)
		return
	}
	dados := comando.Dados
	if dados["acao"] == "encerrar_alerta" {
		fmt.Printf("\n  ALERTA ENCERRADO — %s (%v)\n\n", strings.ToUpper(t.Dispositivo), dados["alarme_id"])
		s.mu.Lock()
		s.estados[t.Dispositivo].alertaPendente = false
		s.mu.Unlock()
		s.publicar(t, "evento", map[string]any{"evento": "recebido", "comando_id": comando.IDMensagem})
		return
	}
	fmt.Printf("\n%s\n  ALERTA RECEBIDO — %s\n  Ação       : %v\n  Severidade : %v\n  Resumo     : %v\n%s\n\n",
		strings.Repeat("=", 60), strings.ToUpper(t.Dispositivo), dados["acao"], dados["severidade"], dados["resumo"],
		strings.Repeat("=", 60))

	s.mu.Lock()
	s.estados[t.Dispositivo].alertaPendente = true
	s.estados[t.Dispositivo].ultimoAlarmeID = dados["alarme_id"]
	s.mu.Unlock()

	s.publicar(t, "evento", map[string]any{"evento": "recebido", "comando_id": comando.IDMensagem})
	if s.autoReconhecer > 0 {
		time.AfterFunc(s.autoReconhecer, func() { s.reconhecer(t, comando) })
	}
}

// reconhecer simula o operador confirmando o alerta (na A5 isso vem do painel).
func (s *Simulador) reconhecer(t Terminal, comando Mensagem) {
	s.mu.Lock()
	s.estados[t.Dispositivo].alertaPendente = false
	s.mu.Unlock()
	s.publicar(t, "evento", map[string]any{
		"evento":     "reconhecido",
		"comando_id": comando.IDMensagem,
		"alarme_id":  comando.Dados["alarme_id"],
		"por":        "operador-simulado",
	})
	log.Printf("[%s] alerta reconhecido", t.Dispositivo)
}

func (s *Simulador) publicarEstados() {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, t := range s.terminais {
		e := s.estados[t.Dispositivo]
		s.publicar(t, "estado", map[string]any{
			"online":           true,
			"alerta_pendente":  e.alertaPendente,
			"ultimo_alarme_id": e.ultimoAlarmeID,
		})
	}
}

func main() {
	log.SetFlags(log.Ltime)
	log.SetPrefix("[terminal] ")

	s := &Simulador{
		terminais:       map[string]Terminal{},
		estados:         map[string]*estadoTerminal{},
		autoReconhecer:  envSegundos("AUTO_RECONHECER_S", 0),
		intervaloEstado: envSegundos("ESTADO_INTERVALO_S", 30),
	}
	for _, prefixo := range strings.Split(env("TERMINAIS", ""), ",") {
		if strings.TrimSpace(prefixo) == "" {
			continue
		}
		t, err := ParseTerminal(prefixo)
		if err != nil {
			log.Fatal(err)
		}
		s.terminais[t.Topico("comando")] = t
		s.estados[t.Dispositivo] = &estadoTerminal{}
	}
	if len(s.terminais) == 0 {
		log.Fatal("TERMINAIS vazio (ex.: pref-anapolis/defesa-civil/alerta_publico/terminal-dc-anapolis)")
	}

	opcoes := mqtt.NewClientOptions().
		AddBroker(fmt.Sprintf("tcp://%s:%s", env("MQTT_BROKER", "localhost"), env("MQTT_PORT", "1883"))).
		SetClientID(env("MQTT_CLIENT_ID", "terminais-alerta")).
		SetCleanSession(false). // sessão persistente: alerta enviado com o terminal offline chega na reconexão
		SetAutoReconnect(true).
		SetConnectRetry(true).
		SetConnectRetryInterval(3 * time.Second).
		SetOnConnectHandler(func(c mqtt.Client) {
			for topico := range s.terminais {
				c.Subscribe(topico, 1, s.aoReceberComando)
			}
			log.Printf("conectado; %d terminal(is) aguardando alertas", len(s.terminais))
		})
	s.cliente = mqtt.NewClient(opcoes)
	if token := s.cliente.Connect(); token.Wait() && token.Error() != nil {
		log.Fatal(token.Error())
	}

	sinais := make(chan os.Signal, 1)
	signal.Notify(sinais, syscall.SIGINT, syscall.SIGTERM)
	relogio := time.NewTicker(s.intervaloEstado)
	defer relogio.Stop()
	s.publicarEstados()
	for {
		select {
		case <-relogio.C:
			s.publicarEstados()
		case <-sinais:
			log.Print("encerrando")
			s.cliente.Disconnect(500)
			return
		}
	}
}
