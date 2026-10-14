package codex

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/anoyou/telepilot/gateway/contract"
	"github.com/anoyou/telepilot/gateway/internal/control"
	"github.com/anoyou/telepilot/gateway/internal/diagnostics"
	"github.com/anoyou/telepilot/gateway/internal/routing"
	"github.com/anoyou/telepilot/gateway/internal/security"
	"github.com/anoyou/telepilot/gateway/internal/version"
)

const (
	maxRequestBodyBytes           = 40 << 20
	maxResponseBodyBytes          = 16 << 20
	upstreamResponseHeaderTimeout = 15 * time.Second
	sseFirstEventTimeout          = 20 * time.Second
	sseIdleTimeout                = 30 * time.Second
	// 请求级预算再长，首包 / idle 也不能无限放宽，否则会长期占用连接。
	maxRequestedSSEFirstEvent = 60 * time.Second
	maxRequestedSSEIdle       = 120 * time.Second
)

type Handler struct {
	store          *control.Store
	mu             sync.Mutex
	providerSem    map[int64]chan struct{}
	clientForRoute func(routing.Route) *http.Client
	pool           *upstreamPool
	sseFirstEvent  time.Duration
	sseIdle        time.Duration
	sseMaxBytes    int64
}

func NewHandler(store *control.Store) *Handler {
	return &Handler{
		store:          store,
		providerSem:    make(map[int64]chan struct{}),
		clientForRoute: httpClient,
		pool:           newUpstreamPool(),
		sseFirstEvent:  sseFirstEventTimeout,
		sseIdle:        sseIdleTimeout,
		sseMaxBytes:    maxResponseBodyBytes,
	}
}

// Close 退役上游连接池，供进程优雅退出时调用（收尾 SIGTERM 之后的空闲 socket）。
func (h *Handler) Close() {
	if h.pool != nil {
		h.pool.close()
	}
}

// upstreamClient 返回该路由的上游 Client。默认走连接池；测试注入 clientForRoute 时
// 仍按原路径返回，保持既有用例可替换 Transport。
func (h *Handler) upstreamClient(route routing.Route, revision int64) *http.Client {
	if h.clientForRoute != nil {
		return h.clientForRoute(route)
	}
	if h.pool == nil {
		return httpClient(route)
	}
	return h.pool.acquire(route, revision)
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch {
	case r.Method == http.MethodPost && r.URL.Path == "/v1/responses":
		h.responses(w, r)
	case r.Method == http.MethodGet && r.URL.Path == "/v1/models":
		h.models(w, r)
	default:
		writeError(w, http.StatusNotFound, contract.GatewayError{Code: "endpoint_missing", Message: "Gateway endpoint not found", RequestID: requestID(r), GatewayStage: "routing"})
	}
}

func (h *Handler) responses(w http.ResponseWriter, r *http.Request) {
	table := h.store.Current()
	if table == nil {
		writeError(w, http.StatusServiceUnavailable, contract.GatewayError{Code: "gateway_unavailable", Message: "Provider snapshot is not ready", Retryable: true, RequestID: requestID(r), GatewayStage: "config"})
		return
	}
	providerID, err := providerID(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, contract.GatewayError{Code: "request_invalid", Message: err.Error(), RequestID: requestID(r), GatewayStage: "routing"})
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxRequestBodyBytes))
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, contract.GatewayError{Code: "request_invalid", Message: "Responses request is too large", RequestID: requestID(r), GatewayStage: "routing"})
		return
	}
	var payload map[string]any
	if json.Unmarshal(body, &payload) != nil {
		writeError(w, http.StatusBadRequest, contract.GatewayError{Code: "request_invalid", Message: "Responses body must be a JSON object", RequestID: requestID(r), GatewayStage: "routing"})
		return
	}
	model, _ := payload["model"].(string)
	route, ok := table.Resolve(providerID, model)
	if !ok {
		writeError(w, http.StatusNotFound, contract.GatewayError{Code: "model_missing", Message: "Model is not configured for this Provider", RequestID: requestID(r), GatewayStage: "routing"})
		return
	}
	release, ok := h.acquire(route)
	if !ok {
		writeError(w, http.StatusServiceUnavailable, contract.GatewayError{Code: "gateway_overloaded", Message: "Provider concurrency limit reached", Retryable: true, RequestID: requestID(r), GatewayStage: "admission"})
		return
	}
	defer release()
	requestContext, cancelRequest := context.WithTimeout(r.Context(), routeTimeoutForRequest(route, r))
	defer cancelRequest()

	downstreamStream, _ := payload["stream"].(bool)
	payload["model"] = route.UpstreamModel
	// 固定上游为 SSE：既匹配 Codex executor，也允许非流式调用由 Gateway 聚合终态。
	payload["stream"] = true
	if _, exists := payload["instructions"]; !exists {
		payload["instructions"] = ""
	}
	identity := buildRequestIdentity(route, r)
	upstreamRequest, err := http.NewRequestWithContext(requestContext, http.MethodPost, route.BaseURL+"/responses", nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "gateway_unavailable", Message: "Failed to create upstream request", Retryable: true, RequestID: requestID(r), GatewayStage: "request"})
		return
	}
	upstreamRequest.Header.Set("Authorization", "Bearer "+route.APIKey)
	upstreamRequest.Header.Set("Content-Type", "application/json")
	upstreamRequest.Header.Set("Accept", "text/event-stream")
	compatibilityHeaders, scopeErr := compatibilityHeadersForRequest(route, r)
	if scopeErr != nil {
		writeError(w, http.StatusBadRequest, contract.GatewayError{Code: "request_invalid", Message: scopeErr.Error(), RequestID: requestID(r), GatewayStage: "routing"})
		return
	}
	route.CompatibilityHeaders = compatibilityHeaders
	for name, value := range compatibilityHeaders {
		upstreamRequest.Header.Set(name, value)
	}
	applyCodexIdentity(payload, upstreamRequest, identity, route.CodexClientVersion)
	security.StripInternalHeaders(upstreamRequest.Header)
	upstreamBody, _ := json.Marshal(payload)
	upstreamRequest.Body = io.NopCloser(bytes.NewReader(upstreamBody))
	upstreamRequest.ContentLength = int64(len(upstreamBody))

	client := h.upstreamClient(route, table.Revision())
	response, err := doUpstreamRequest(client, upstreamRequest, requestContext)
	if err != nil {
		writeUpstreamRequestError(w, r, err, route)
		return
	}
	defer response.Body.Close()
	w.Header().Set("X-TelePilot-Gateway-Version", version.Release)
	w.Header().Set("X-TelePilot-Gateway-Request-ID", requestID(r))
	w.Header().Set("X-TelePilot-Gateway-Stage", "upstream")
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		errorBody, _ := io.ReadAll(io.LimitReader(response.Body, 1<<20))
		gatewayError := diagnostics.FromUpstream(response.StatusCode, errorBody, requestID(r), routeSecretValues(route)...)
		writeError(w, normalizedUpstreamStatus(response.StatusCode), diagnostics.WithUpstreamHeaders(gatewayError, response.Header))
		return
	}
	contentType := strings.ToLower(response.Header.Get("Content-Type"))
	if strings.Contains(contentType, "text/event-stream") {
		sseFirstEvent, sseIdle := h.sseTimeoutsForRequest(r)
		if downstreamStream {
			w.Header().Set("Content-Type", "text/event-stream")
			wrote, streamErr := copySSEWithLimits(
				w,
				response.Body,
				route.UpstreamModel,
				model,
				sseFirstEvent,
				sseIdle,
				h.sseMaxBytes,
				routeSecretValues(route)...,
			)
			if streamErr != nil && !wrote && r.Context().Err() == nil {
				writeSSEReadError(w, r, streamErr, requestContext)
			}
			return
		}
		final, err := aggregateSSEWithLimits(
			response.Body,
			route.UpstreamModel,
			model,
			sseFirstEvent,
			sseIdle,
			h.sseMaxBytes,
		)
		if err != nil {
			var failed *upstreamEventFailure
			if errors.As(err, &failed) {
				gatewayError := diagnostics.FromUpstream(http.StatusBadGateway, failed.body, requestID(r), routeSecretValues(route)...)
				writeError(w, http.StatusBadGateway, diagnostics.WithUpstreamHeaders(gatewayError, response.Header))
				return
			}
			if isSSETimeout(err, requestContext) {
				writeError(w, http.StatusGatewayTimeout, contract.GatewayError{Code: "timeout", Message: "Upstream response stream timed out", Retryable: true, RequestID: requestID(r), GatewayStage: "response"})
				return
			}
			writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "invalid_response", Message: err.Error(), RequestID: requestID(r), GatewayStage: "response"})
			return
		}
		writeJSON(w, http.StatusOK, final)
		return
	}
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, maxResponseBodyBytes+1))
	if err != nil || len(responseBody) > maxResponseBodyBytes {
		writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "invalid_response", Message: "Upstream response could not be read", RequestID: requestID(r), GatewayStage: "response"})
		return
	}
	restored := restoreModel(responseBody, route.UpstreamModel, model)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(restored)
}

func (h *Handler) models(w http.ResponseWriter, r *http.Request) {
	table := h.store.Current()
	if table == nil {
		writeError(w, http.StatusServiceUnavailable, contract.GatewayError{Code: "gateway_unavailable", Message: "Provider snapshot is not ready", Retryable: true, RequestID: requestID(r), GatewayStage: "config"})
		return
	}
	providerID, err := providerID(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, contract.GatewayError{Code: "request_invalid", Message: err.Error(), RequestID: requestID(r), GatewayStage: "routing"})
		return
	}
	route, ok := table.ProviderRoute(providerID)
	if !ok {
		writeError(w, http.StatusNotFound, contract.GatewayError{Code: "provider_missing", Message: "Provider route is unavailable", RequestID: requestID(r), GatewayStage: "routing"})
		return
	}
	release, ok := h.acquire(route)
	if !ok {
		writeError(w, http.StatusServiceUnavailable, contract.GatewayError{Code: "gateway_overloaded", Message: "Provider concurrency limit reached", Retryable: true, RequestID: requestID(r), GatewayStage: "admission"})
		return
	}
	defer release()
	requestContext, cancelRequest := context.WithTimeout(r.Context(), routeTimeout(route))
	defer cancelRequest()
	client := h.upstreamClient(route, table.Revision())
	var response *http.Response
	for index, endpoint := range route.ModelsEndpoints {
		upstreamRequest, requestErr := http.NewRequestWithContext(requestContext, http.MethodGet, endpoint, nil)
		if requestErr != nil {
			writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "gateway_unavailable", Message: "Failed to create upstream request", Retryable: true, RequestID: requestID(r), GatewayStage: "request"})
			return
		}
		upstreamRequest.Header.Set("Authorization", "Bearer "+route.APIKey)
		upstreamRequest.Header.Set("Accept", "application/json")
		for name, value := range route.CompatibilityHeaders {
			upstreamRequest.Header.Set(name, value)
		}
		identity := buildRequestIdentity(route, r)
		applyCodexHeaders(upstreamRequest, identity, route.CodexClientVersion)
		security.StripInternalHeaders(upstreamRequest.Header)
		response, err = doUpstreamRequest(client, upstreamRequest, requestContext)
		if err != nil {
			writeUpstreamRequestError(w, r, err, route)
			return
		}
		if (response.StatusCode == http.StatusNotFound || response.StatusCode == http.StatusMethodNotAllowed) && index < len(route.ModelsEndpoints)-1 {
			_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1<<20))
			_ = response.Body.Close()
			continue
		}
		break
	}
	if response == nil {
		writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "gateway_unavailable", Message: "No upstream model endpoint is configured", Retryable: false, RequestID: requestID(r), GatewayStage: "config"})
		return
	}
	defer response.Body.Close()
	setGatewayHeaders(w, requestID(r), "upstream")
	body, readErr := io.ReadAll(io.LimitReader(response.Body, maxResponseBodyBytes+1))
	if readErr != nil || len(body) > maxResponseBodyBytes {
		writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "invalid_response", Message: "Upstream model list could not be read", RequestID: requestID(r), GatewayStage: "response"})
		return
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		gatewayError := diagnostics.FromUpstream(response.StatusCode, body, requestID(r), routeSecretValues(route)...)
		writeError(w, normalizedUpstreamStatus(response.StatusCode), diagnostics.WithUpstreamHeaders(gatewayError, response.Header))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}

func (h *Handler) acquire(route routing.Route) (func(), bool) {
	limit := route.MaxConcurrency
	if limit < 1 {
		limit = 8
	}
	h.mu.Lock()
	sem := h.providerSem[route.ProviderID]
	if sem == nil || cap(sem) != limit {
		sem = make(chan struct{}, limit)
		h.providerSem[route.ProviderID] = sem
	}
	h.mu.Unlock()
	select {
	case sem <- struct{}{}:
		return func() { <-sem }, true
	default:
		return nil, false
	}
}

func providerID(r *http.Request) (int64, error) {
	value := r.Header.Get("X-TelePilot-Provider-ID")
	id, err := strconv.ParseInt(value, 10, 64)
	if err != nil || id < 1 {
		return 0, errors.New("X-TelePilot-Provider-ID is required")
	}
	return id, nil
}

func requestID(r *http.Request) string {
	return strings.TrimSpace(r.Header.Get("X-TelePilot-Request-ID"))
}

// requestTimeout 返回调用方下发的请求级总超时（秒）。
// 0 表示没有下发，调用方沿用 route snapshot 的默认值。
func requestTimeout(r *http.Request) time.Duration {
	raw := strings.TrimSpace(r.Header.Get("X-TelePilot-Timeout-Seconds"))
	if raw == "" {
		return 0
	}
	seconds, err := strconv.Atoi(raw)
	if err != nil || seconds <= 0 {
		return 0
	}
	return time.Duration(seconds) * time.Second
}

func compatibilityHeadersForRequest(route routing.Route, r *http.Request) (map[string]string, error) {
	switch strings.TrimSpace(r.Header.Get("X-TelePilot-Request-Scope")) {
	case "", "inference":
		return route.CompatibilityHeaders, nil
	case "liveness":
		return route.LivenessCompatibilityHeaders, nil
	default:
		return nil, errors.New("X-TelePilot-Request-Scope must be inference or liveness")
	}
}

func routeSecretValues(route routing.Route) []string {
	values := make(
		[]string,
		0,
		len(route.CompatibilityHeaders)+
			len(route.LivenessCompatibilityHeaders)+
			len(route.ModelsEndpoints)+3,
	)
	values = append(values, route.APIKey, route.ProxyURL, route.BaseURL)
	values = append(values, route.ModelsEndpoints...)
	for _, headers := range []map[string]string{
		route.CompatibilityHeaders,
		route.LivenessCompatibilityHeaders,
	} {
		for _, value := range headers {
			values = append(values, value)
		}
	}
	return values
}

func writeUpstreamRequestError(w http.ResponseWriter, r *http.Request, err error, route routing.Route) {
	if r.Context().Err() != nil {
		return
	}
	if errors.Is(err, context.DeadlineExceeded) {
		writeError(w, http.StatusGatewayTimeout, contract.GatewayError{Code: "timeout", Message: "Upstream request timed out", Retryable: true, RequestID: requestID(r), GatewayStage: "upstream"})
		return
	}
	// net/http 的错误字符串通常带完整目标 URL。该 URL 可能包含租户路径或
	// query，不能进入 API、usage 或 UI；具体异常只留在不含请求目标的稳定码中。
	writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "network_error", Message: "Upstream network request failed", Retryable: true, RequestID: requestID(r), GatewayStage: "upstream"})
}

func normalizedUpstreamStatus(status int) int {
	if status < 400 || status > 599 {
		return http.StatusBadGateway
	}
	return status
}

func doUpstreamRequest(client *http.Client, request *http.Request, downstream context.Context) (*http.Response, error) {
	transport, canCancel := client.Transport.(interface{ CancelRequest(*http.Request) })
	if !canCancel {
		return client.Do(request)
	}
	stopCancel := context.AfterFunc(downstream, func() {
		transport.CancelRequest(request)
	})
	defer stopCancel()
	return client.Do(request)
}

// httpClient 返回单个一次性上游 Client。生产路径已改走 Handler.upstreamClient
// （连接池复用），这里保留给测试和需要显式一次性连接的调用方。
func httpClient(route routing.Route) *http.Client {
	return newUpstreamClient(route)
}

func routeTimeout(route routing.Route) time.Duration {
	timeout := time.Duration(route.TimeoutSeconds) * time.Second
	if timeout <= 0 {
		return 90 * time.Second
	}
	return timeout
}

// routeTimeoutForRequest 让请求级参数优先于 snapshot 默认值：
// 调用方（System Agent 等）带 X-TelePilot-Timeout-Seconds 时按它的总预算走，
// 否则保持 snapshot timeout_seconds（或 90s 兜底），向后兼容。
func routeTimeoutForRequest(route routing.Route, r *http.Request) time.Duration {
	if requested := requestTimeout(r); requested > 0 {
		return requested
	}
	return routeTimeout(route)
}

// sseTimeoutsForRequest 把首包 / idle 超时一起按请求级总预算收紧：
// 总预算更长时首包与 idle 相应放宽（不超过 60s），避免长请求被固定 20s/30s 掐断。
func (h *Handler) sseTimeoutsForRequest(r *http.Request) (time.Duration, time.Duration) {
	requested := requestTimeout(r)
	if requested <= 0 {
		return h.sseFirstEvent, h.sseIdle
	}
	first := h.sseFirstEvent
	idle := h.sseIdle
	if limit := requested / 6; limit > first {
		first = limit
	}
	if limit := requested / 3; limit > idle {
		idle = limit
	}
	if first > maxRequestedSSEFirstEvent {
		first = maxRequestedSSEFirstEvent
	}
	if idle > maxRequestedSSEIdle {
		idle = maxRequestedSSEIdle
	}
	return first, idle
}

func copySSE(
	w io.Writer,
	reader io.Reader,
	upstreamModel,
	publicModel string,
	knownSecrets ...string,
) error {
	_, err := copySSEWithLimits(
		w, reader, upstreamModel, publicModel,
		sseFirstEventTimeout, sseIdleTimeout, maxResponseBodyBytes, knownSecrets...,
	)
	return err
}

func copySSEWithLimits(
	w io.Writer,
	reader io.Reader,
	upstreamModel,
	publicModel string,
	firstEventTimeout,
	idleTimeout time.Duration,
	maxBytes int64,
	knownSecrets ...string,
) (bool, error) {
	flusher, _ := w.(http.Flusher)
	scanner, counted, timed := newSSEScanner(reader, firstEventTimeout, idleTimeout, maxBytes)
	wrote := false
	var pending bytes.Buffer
	for scanner.Scan() {
		if counted.read > maxBytes {
			return wrote, &sseSizeError{limit: maxBytes}
		}
		line := scanner.Bytes()
		if bytes.HasPrefix(line, []byte("data:")) {
			timed.markStarted()
			data := bytes.TrimSpace(bytes.TrimPrefix(line, []byte("data:")))
			if !bytes.Equal(data, []byte("[DONE]")) {
				data = restoreModel(data, upstreamModel, publicModel)
				data = redactFailedSSEEvent(data, knownSecrets...)
			}
			line = append([]byte("data: "), data...)
		}
		rendered := append(append([]byte(nil), line...), '\n')
		if !timed.started {
			_, _ = pending.Write(rendered)
			continue
		}
		if pending.Len() > 0 {
			if _, err := w.Write(pending.Bytes()); err != nil {
				return wrote, err
			}
			wrote = true
			pending.Reset()
		}
		if _, err := w.Write(rendered); err != nil {
			return wrote, err
		}
		wrote = true
		if flusher != nil {
			flusher.Flush()
		}
	}
	if counted.read > maxBytes {
		return wrote, &sseSizeError{limit: maxBytes}
	}
	return wrote, scanner.Err()
}

func redactFailedSSEEvent(data []byte, knownSecrets ...string) []byte {
	var event map[string]any
	if json.Unmarshal(data, &event) != nil || event["type"] != "response.failed" {
		return data
	}
	redactJSONValue(event, knownSecrets)
	redacted, err := json.Marshal(event)
	if err != nil {
		return []byte(`{"type":"response.failed","response":{"error":{"message":"Upstream request failed"}}}`)
	}
	return redacted
}

func redactJSONValue(value any, knownSecrets []string) {
	switch typed := value.(type) {
	case map[string]any:
		for childKey, child := range typed {
			if sensitiveJSONKey(childKey) {
				if child != nil && child != "" {
					typed[childKey] = "<redacted>"
				}
				continue
			}
			if text, ok := child.(string); ok {
				typed[childKey] = security.RedactKnown(text, knownSecrets...)
				continue
			}
			redactJSONValue(child, knownSecrets)
		}
	case []any:
		for index, child := range typed {
			if text, ok := child.(string); ok {
				typed[index] = security.RedactKnown(text, knownSecrets...)
				continue
			}
			redactJSONValue(child, knownSecrets)
		}
	}
}

func sensitiveJSONKey(value string) bool {
	normalized := strings.NewReplacer("-", "", "_", "", ".", "").Replace(strings.ToLower(value))
	for _, fragment := range []string{
		"authorization",
		"apikey",
		"accesstoken",
		"refreshtoken",
		"authtoken",
		"bearertoken",
		"bottoken",
		"password",
		"secret",
		"credential",
		"cookie",
		"proxyuser",
		"proxypass",
	} {
		if strings.Contains(normalized, fragment) {
			return true
		}
	}
	return false
}

func aggregateSSE(reader io.Reader, upstreamModel, publicModel string) (json.RawMessage, error) {
	return aggregateSSEWithLimits(
		reader, upstreamModel, publicModel,
		sseFirstEventTimeout, sseIdleTimeout, maxResponseBodyBytes,
	)
}

func aggregateSSEWithLimits(
	reader io.Reader,
	upstreamModel,
	publicModel string,
	firstEventTimeout,
	idleTimeout time.Duration,
	maxBytes int64,
) (json.RawMessage, error) {
	scanner, counted, timed := newSSEScanner(reader, firstEventTimeout, idleTimeout, maxBytes)
	var terminal json.RawMessage
	for scanner.Scan() {
		if counted.read > maxBytes {
			return nil, &sseSizeError{limit: maxBytes}
		}
		line := bytes.TrimSpace(scanner.Bytes())
		if !bytes.HasPrefix(line, []byte("data:")) {
			continue
		}
		timed.markStarted()
		data := bytes.TrimSpace(bytes.TrimPrefix(line, []byte("data:")))
		var event map[string]any
		if json.Unmarshal(data, &event) != nil {
			continue
		}
		typeName, _ := event["type"].(string)
		if typeName == "response.failed" {
			failure := any(event)
			if response, ok := event["response"]; ok {
				failure = response
			}
			body, _ := json.Marshal(failure)
			return nil, &upstreamEventFailure{body: body}
		}
		if typeName == "response.completed" || typeName == "response.incomplete" {
			response, ok := event["response"]
			if !ok {
				response = event
			}
			terminal, _ = json.Marshal(response)
		}
	}
	if counted.read > maxBytes {
		return nil, &sseSizeError{limit: maxBytes}
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if len(terminal) == 0 {
		return nil, errors.New("upstream SSE ended without a terminal response")
	}
	return restoreModel(terminal, upstreamModel, publicModel), nil
}

type sseReadResult struct {
	data []byte
	err  error
}

type sseTimeoutError struct {
	phase string
}

func (failure *sseTimeoutError) Error() string {
	return "upstream SSE " + failure.phase + " timed out"
}

type sseSizeError struct {
	limit int64
}

func (failure *sseSizeError) Error() string {
	return fmt.Sprintf("upstream SSE exceeds %d bytes", failure.limit)
}

type sseTimeoutReader struct {
	reader        io.Reader
	firstTimeout  time.Duration
	idleTimeout   time.Duration
	started       bool
	firstDeadline time.Time
	idleDeadline  time.Time
}

func (reader *sseTimeoutReader) Read(buffer []byte) (int, error) {
	timeout := reader.firstTimeout
	phase := "first event"
	if reader.started {
		phase = "idle"
		if reader.idleDeadline.IsZero() && reader.idleTimeout > 0 {
			reader.idleDeadline = time.Now().Add(reader.idleTimeout)
		}
		if !reader.idleDeadline.IsZero() {
			timeout = time.Until(reader.idleDeadline)
		} else {
			timeout = reader.idleTimeout
		}
	} else if !reader.firstDeadline.IsZero() {
		timeout = time.Until(reader.firstDeadline)
	}
	if timeout <= 0 {
		if (!reader.started && !reader.firstDeadline.IsZero()) ||
			(reader.started && !reader.idleDeadline.IsZero()) {
			return 0, &sseTimeoutError{phase: phase}
		}
		return reader.reader.Read(buffer)
	}
	if !reader.started && reader.firstDeadline.IsZero() {
		reader.firstDeadline = time.Now().Add(timeout)
	}
	result := make(chan sseReadResult, 1)
	temporary := make([]byte, len(buffer))
	go func() {
		n, err := reader.reader.Read(temporary)
		result <- sseReadResult{data: temporary[:n], err: err}
	}()
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case completed := <-result:
		copy(buffer, completed.data)
		return len(completed.data), completed.err
	case <-timer.C:
		return 0, &sseTimeoutError{phase: phase}
	}
}

func (reader *sseTimeoutReader) markStarted() {
	reader.started = true
	if reader.idleTimeout > 0 {
		reader.idleDeadline = time.Now().Add(reader.idleTimeout)
	}
}

type sseCountingReader struct {
	reader io.Reader
	read   int64
}

func (reader *sseCountingReader) Read(buffer []byte) (int, error) {
	n, err := reader.reader.Read(buffer)
	reader.read += int64(n)
	return n, err
}

func newSSEScanner(
	reader io.Reader,
	firstEventTimeout, idleTimeout time.Duration,
	maxBytes int64,
) (*bufio.Scanner, *sseCountingReader, *sseTimeoutReader) {
	limited := &io.LimitedReader{R: reader, N: maxBytes + 1}
	timed := &sseTimeoutReader{
		reader: limited, firstTimeout: firstEventTimeout, idleTimeout: idleTimeout,
	}
	counted := &sseCountingReader{reader: timed}
	scanner := bufio.NewScanner(counted)
	maxInt := int64(^uint(0) >> 1)
	maxTokenBytes := maxBytes + 1
	if maxTokenBytes <= 0 || maxTokenBytes > maxInt {
		maxTokenBytes = maxInt
	}
	initialBytes := int64(64 << 10)
	if maxTokenBytes < initialBytes {
		initialBytes = maxTokenBytes
	}
	// Let the total-response limit, rather than Scanner's legacy 1 MiB
	// token limit, classify a large but still permitted SSE event.
	scanner.Buffer(make([]byte, int(initialBytes)), int(maxTokenBytes))
	return scanner, counted, timed
}

func isSSETimeout(err error, requestContext context.Context) bool {
	var timeout *sseTimeoutError
	return errors.As(err, &timeout) || errors.Is(err, context.DeadlineExceeded) || errors.Is(requestContext.Err(), context.DeadlineExceeded)
}

func writeSSEReadError(w http.ResponseWriter, r *http.Request, err error, requestContext context.Context) {
	if isSSETimeout(err, requestContext) {
		writeError(w, http.StatusGatewayTimeout, contract.GatewayError{Code: "timeout", Message: "Upstream response stream timed out", Retryable: true, RequestID: requestID(r), GatewayStage: "response"})
		return
	}
	writeError(w, http.StatusBadGateway, contract.GatewayError{Code: "invalid_response", Message: err.Error(), RequestID: requestID(r), GatewayStage: "response"})
}

type upstreamEventFailure struct {
	body []byte
}

func (failure *upstreamEventFailure) Error() string {
	return "upstream stream ended with response.failed"
}

func restoreModel(body []byte, upstreamModel, publicModel string) []byte {
	if upstreamModel == publicModel || len(body) == 0 {
		return body
	}
	var payload any
	if json.Unmarshal(body, &payload) != nil {
		return body
	}
	restoreModelValue(payload, upstreamModel, publicModel)
	updated, err := json.Marshal(payload)
	if err != nil {
		return body
	}
	return updated
}

func restoreModelValue(value any, upstreamModel, publicModel string) {
	switch typed := value.(type) {
	case map[string]any:
		if model, ok := typed["model"].(string); ok && model == upstreamModel {
			typed["model"] = publicModel
		}
		for _, child := range typed {
			restoreModelValue(child, upstreamModel, publicModel)
		}
	case []any:
		for _, child := range typed {
			restoreModelValue(child, upstreamModel, publicModel)
		}
	}
}

func writeError(w http.ResponseWriter, status int, gatewayError contract.GatewayError) {
	setGatewayHeaders(w, gatewayError.RequestID, gatewayError.GatewayStage)
	writeJSON(w, status, contract.ErrorEnvelope{Error: gatewayError})
}

func setGatewayHeaders(w http.ResponseWriter, requestID, stage string) {
	w.Header().Set("X-TelePilot-Gateway-Version", version.Release)
	if requestID != "" {
		w.Header().Set("X-TelePilot-Gateway-Request-ID", requestID)
	}
	if stage != "" {
		w.Header().Set("X-TelePilot-Gateway-Stage", stage)
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
