package contract

const ProtocolVersion = "2"

// DefaultCodexClientVersion 是控制面快照缺少 codex_client_version 时的兼容回退值，
// 也是 gateway 内唯一的 Codex TUI 版本字面量来源（routing 表的兜底、Codex 身份
// 请求头的缺省版本都读这里）。
//
// 该值必须与 backend/app/services/llm_identity.py 的 codex_tui 默认版本保持一致：
// Python 控制面下发快照时会带上当前生效版本，这个常量只在"旧控制面 / 测试构造的
// 快照缺字段"时才被用到。两侧漂移的症状是 UA / Version 头与 Python 链路不一致，
// 上游可能据此拒绝或降级处理。改这里时同步改 llm_identity.py 与对应回归测试。
const DefaultCodexClientVersion = "0.145.0"

type VersionInfo struct {
	Version                 string `json:"version"`
	ProtocolVersion         string `json:"gateway_protocol_version"`
	UpstreamCommit          string `json:"upstream_commit"`
	BuildCommit             string `json:"build_commit"`
	CodexContractReviewDate string `json:"codex_contract_review_date"`
}

type ErrorEnvelope struct {
	Error GatewayError `json:"error"`
}

type GatewayError struct {
	Code                 string `json:"code"`
	Message              string `json:"message"`
	Retryable            bool   `json:"retryable"`
	StatusCode           int    `json:"status_code,omitempty"`
	UpstreamStatusCode   int    `json:"upstream_status_code,omitempty"`
	UpstreamErrorCode    string `json:"upstream_error_code,omitempty"`
	UpstreamErrorMessage string `json:"upstream_error_message,omitempty"`
	UpstreamErrorDetail  string `json:"upstream_error_detail,omitempty"`
	UpstreamRequestID    string `json:"upstream_request_id,omitempty"`
	ClientRequestID      string `json:"client_request_id,omitempty"`
	RequestID            string `json:"request_id,omitempty"`
	GatewayStage         string `json:"gateway_stage,omitempty"`
}

type ConfigSnapshot struct {
	SchemaVersion      int              `json:"schema_version"`
	ProtocolVersion    string           `json:"gateway_protocol_version"`
	CodexClientVersion string           `json:"codex_client_version,omitempty"`
	Revision           int64            `json:"revision"`
	Providers          []ProviderConfig `json:"providers"`
}

type ProviderConfig struct {
	ID                           int64             `json:"id"`
	BaseURL                      string            `json:"base_url"`
	APIKey                       string            `json:"api_key"`
	Models                       []string          `json:"models"`
	ModelMapping                 map[string]string `json:"model_mapping,omitempty"`
	ProxyURL                     string            `json:"proxy_url,omitempty"`
	TimeoutSeconds               int               `json:"timeout_seconds,omitempty"`
	CompatibilityHeaders         map[string]string `json:"compatibility_headers,omitempty"`
	LivenessCompatibilityHeaders map[string]string `json:"liveness_compatibility_headers,omitempty"`
	ModelsCompatibilityHeaders   map[string]string `json:"models_compatibility_headers,omitempty"`
	ModelsEndpoints              []string          `json:"models_endpoints,omitempty"`
	MaxConcurrency               int               `json:"max_concurrency,omitempty"`
}

type ConfigStatus struct {
	Ready              bool   `json:"ready"`
	Revision           int64  `json:"revision"`
	ProviderCount      int    `json:"provider_count"`
	CodexClientVersion string `json:"codex_client_version,omitempty"`
	SyncedAt           string `json:"synced_at,omitempty"`
	Error              string `json:"error,omitempty"`
}
