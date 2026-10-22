// Dispositivo de teste em Go: só a biblioteca genérica paho.mqtt.golang e net/http.
//
//	go run .   (com SLUG e CREDENCIAL no ambiente)
package main

import (
	"bytes"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"

	mqtt "github.com/eclipse/paho.mqtt.golang"
)

func env(nome, padrao string) string {
	if v := os.Getenv(nome); v != "" {
		return v
	}
	if padrao == "" {
		log.Fatalf("defina %s", nome)
	}
	return padrao
}

var (
	slug       = env("SLUG", "")
	credencial = env("CREDENCIAL", "")
	base       = fmt.Sprintf("%s/%s/%s/%s", env("INQUILINO", "brigada-cerrado"), env("SITE", "mirador"), env("DOMINIO", "incendio"), slug)
)

// uuid v4 sem dependência extra
func novoID() string {
	b := make([]byte, 16)
	rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

// mensagem canônica v1.0 (contratos/mensagem-canonica.v1.schema.json)
func mensagem(tipo string, extra map[string]any) map[string]any {
	m := map[string]any{
		"versao":      "1.0",
		"id_mensagem": novoID(), // gerado na origem: reenviar o mesmo id não duplica
		"dispositivo": slug,
		"tipo":        tipo,
		"timestamp":   time.Now().UTC().Format("2006-01-02T15:04:05.000Z"),
	}
	for k, v := range extra {
		m[k] = v
	}
	return m
}

func leitura(temperatura float64) map[string]any {
	return mensagem("telemetria", map[string]any{"medidas": []map[string]any{
		{"grandeza": "temperatura", "valor": temperatura, "unidade": "Cel"},
		{"grandeza": "umidade", "valor": 40.0, "unidade": "%"},
		{"grandeza": "fumaca", "valor": 5.0, "unidade": "1"},
	}})
}

func main() {
	ca, err := os.ReadFile(env("MQTT_CA", "plataforma/infra/pki/gerado/ca.crt"))
	if err != nil {
		log.Fatal(err)
	}
	cas := x509.NewCertPool()
	cas.AppendCertsFromPEM(ca)

	opcoes := mqtt.NewClientOptions().
		AddBroker(fmt.Sprintf("ssl://%s:%s", env("MQTT_HOST", "localhost"), env("MQTT_PORT", "8883"))).
		SetClientID(slug).SetUsername(slug).SetPassword(credencial).
		SetTLSConfig(&tls.Config{RootCAs: cas, MinVersion: tls.VersionTLS12})
	cliente := mqtt.NewClient(opcoes)
	if t := cliente.Connect(); t.Wait() && t.Error() != nil {
		log.Fatal(t.Error())
	}
	fmt.Println("MQTT conectado")

	cliente.Subscribe(base+"/comando", 1, func(c mqtt.Client, msg mqtt.Message) {
		var comando map[string]any
		json.Unmarshal(msg.Payload(), &comando)
		fmt.Println("comando recebido:", comando["dados"])
		resposta, _ := json.Marshal(mensagem("evento", map[string]any{
			"dados": map[string]any{"evento": "executado", "comando_id": comando["id_mensagem"]},
		}))
		c.Publish(base+"/evento", 1, false, resposta)
	}).Wait()

	for _, temperatura := range []float64{24, 25.5, 26.1} {
		carga, _ := json.Marshal(leitura(temperatura))
		cliente.Publish(base+"/telemetria", 1, false, carga).Wait()
		fmt.Printf("MQTT: telemetria publicada (%.1f °C)\n", temperatura)
	}

	corpo, _ := json.Marshal(leitura(27))
	req, _ := http.NewRequest("POST", env("API", "http://localhost:8000")+"/api/v1/ingestao", bytes.NewReader(corpo))
	req.Header.Set("Content-Type", "application/json")
	req.SetBasicAuth(slug, credencial)
	resposta, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Fatal(err)
	}
	texto, _ := io.ReadAll(resposta.Body)
	fmt.Printf("HTTP: %d %s\n", resposta.StatusCode, texto)

	fmt.Println("aguardando comandos por 15 s…")
	time.Sleep(15 * time.Second)
	cliente.Disconnect(250)
}
