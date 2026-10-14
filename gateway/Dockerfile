FROM golang:1.23-bookworm AS builder

WORKDIR /src
COPY gateway/go.mod ./gateway/go.mod
COPY gateway/ ./gateway/
WORKDIR /src/gateway
RUN CGO_ENABLED=0 go test ./... && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/telepilot-gateway ./cmd/telepilot-gateway

# distroless/static-debian12 而非 scratch：Gateway 用 Go net/http 访问 HTTPS 上游，
# 需要系统 CA 根证书完成校验。scratch 不带 /etc/ssl/certs，实测会以
# `x509: certificate signed by unknown authority` 失败（见本目录 UPSTREAM.md
# 与本文件提交说明的探针复现）。该基础镜像只多出 CA 与 /etc/passwd，仍无 shell、
# 无包管理器，保持"只复制二进制"的最小运行时。
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /out/telepilot-gateway /telepilot-gateway
ENTRYPOINT ["/telepilot-gateway"]
