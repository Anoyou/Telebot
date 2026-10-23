package codex

import (
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/anoyou/telepilot/gateway/internal/routing"
)

// upstreamPool 按 (provider, proxy, baseURL, snapshot revision) 复用 http.Client。
//
// 背景：原先每个上游请求都 `Close = true` + `defer CloseIdleConnections()`，等于每次
// 调用都新建 TCP + TLS 连接，长任务（System Agent 多轮工具调用）会把握手开销全额付在
// 每一次请求上。改为按路由身份缓存 Client/Transport 后，同一 Provider 的后续请求可以
// 复用已建立的连接。
//
// revision 是关键：Provider 改了代理、Base URL 或凭据后新 snapshot 的 revision 会递增，
// 旧池必须整体退役，不能继续用旧的 dialer/proxy/连接目标。
type upstreamPool struct {
	mu       sync.Mutex
	revision int64
	clients  map[string]*http.Client
}

func newUpstreamPool() *upstreamPool {
	return &upstreamPool{clients: make(map[string]*http.Client)}
}

// poolKey 只挑真正影响连接池的字段：provider、proxy、baseURL、snapshot revision。
// 模型名、超时、并发上限、兼容请求头都不改变 pool 的复用资格，放进 key 会白白打出
// 多个池，把复用效果抵消掉。
// 分隔符用 "\x00"：baseURL/proxy 均为管理员可配字符串，若用 "|" 之类可见字符拼接，
// baseURL 路径里含 "|x" 时两个不同元组可以拼出同一 key，导致共用错误代理的连接池。
func poolKey(route routing.Route, revision int64) string {
	return strings.Join([]string{
		"p" + itoa(route.ProviderID),
		"r" + itoa(revision),
		"b" + route.BaseURL,
		"x" + route.ProxyURL,
	}, "\x00")
}

func itoa(value int64) string {
	if value == 0 {
		return "0"
	}
	negative := value < 0
	if negative {
		value = -value
	}
	var digits []byte
	for value > 0 {
		digits = append([]byte{byte('0' + value%10)}, digits...)
		value /= 10
	}
	if negative {
		return "-" + string(digits)
	}
	return string(digits)
}

// acquire 返回该路由的 Client；revision 变化时先退役整个旧池，再为新 revision 建池。
func (p *upstreamPool) acquire(route routing.Route, revision int64) *http.Client {
	key := poolKey(route, revision)
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.revision != revision {
		p.retireLocked()
		p.revision = revision
	}
	if client, ok := p.clients[key]; ok {
		return client
	}
	client := newUpstreamClient(route)
	p.clients[key] = client
	return client
}

// retireLocked 关闭所有空闲连接并清空池。调用方必须在持锁状态下调用。
func (p *upstreamPool) retireLocked() {
	for _, client := range p.clients {
		if transport, ok := client.Transport.(*http.Transport); ok {
			transport.CloseIdleConnections()
		}
	}
	for key := range p.clients {
		delete(p.clients, key)
	}
}

// close 供进程退出时调用：关闭全部空闲连接，避免 socket 在 SIGTERM 之后仍被占用。
func (p *upstreamPool) close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.retireLocked()
	p.revision = 0
}

// size 返回当前池中的 Client 数，供测试断言复用行为。
func (p *upstreamPool) size() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.clients)
}

// newUpstreamClient 构造带连接复用的上游 Client。Timeout 保持 0：总超时由
// requestContext 控制（请求级覆盖优先），Client 层面不能再压一层。
func newUpstreamClient(route routing.Route) *http.Client {
	transport := &http.Transport{
		Proxy:                 proxyFunc(route.ProxyURL),
		DialContext:           (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:          64,
		MaxIdleConnsPerHost:   16,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: upstreamResponseHeaderTimeout,
		ForceAttemptHTTP2:     true,
	}
	return &http.Client{
		Transport: transport,
		// 上游重定向不自动跟随：自定义认证头跨 origin 泄露是真实风险。
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
}

// proxyFunc 与旧的 http.ProxyURL 语义一致：未显式选择 Provider 代理时必须直连，
// 不能继承 Web/Gateway 进程的 HTTP_PROXY/HTTPS_PROXY 环境。
func proxyFunc(proxyURL string) func(*http.Request) (*url.URL, error) {
	if strings.TrimSpace(proxyURL) == "" {
		return nil
	}
	parsed, err := url.Parse(proxyURL)
	if err != nil {
		return nil
	}
	return http.ProxyURL(parsed)
}
