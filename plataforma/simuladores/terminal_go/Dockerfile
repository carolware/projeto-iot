# Terminal de alerta urbano (Go) — build multi-stage, imagem final mínima.
# Contexto do build: plataforma/ (os testes leem contratos/exemplos).
FROM golang:1.23-alpine AS build

WORKDIR /src/simuladores/terminal_go
COPY simuladores/terminal_go/go.mod simuladores/terminal_go/go.sum ./
RUN go mod download

COPY contratos /src/contratos
COPY simuladores/terminal_go/*.go ./
RUN go vet ./... && go test ./... && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /terminal .

FROM gcr.io/distroless/static-debian12
COPY --from=build /terminal /terminal
ENTRYPOINT ["/terminal"]
