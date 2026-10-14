package codex

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/anoyou/telepilot/gateway/contract"
	"github.com/anoyou/telepilot/gateway/internal/control"
	"github.com/anoyou/telepilot/gateway/internal/routing"
)

// TestUpstreamPoolReusesClientForSameRoute：同一条路由连续两次 acquire 必须拿到同一个
// *http.Client——只有拿到同一个 Client，net/http 才会继续复用底层 TCP/TLS 连接。
func TestUpstreamPoolReusesClientForSameRoute(t *testing.T) {
	pool := newUpstreamPool()
	route := routing.Route{ProviderID: 1, BaseURL: "https://upstream.example"}

	first := pool.acquire(route, 1)
	second := pool.acquire(route, 1)
	if first != second {
		t.Fatal("同一路由的第二个请求新建了 Client，连接无法复用")
	}
	if size := pool.size(); size != 1 {
		t.Fatalf("池内 Client 数应保持 1, got=%d", size)
	}
}

// TestUpstreamPoolSeparatesProvidersAndProxies：不同 provider / proxy / baseURL 必须分开建池，
// 否则凭据和目标会互相串。
func TestUpstreamPoolSeparatesProvidersAndProxies(t *testing.T) {
	pool := newUpstreamPool()
	clients := map[string]*http.Client{}
	for _, route := range []routing.Route{
		{ProviderID: 1, BaseURL: "https://a.example"},
		{ProviderID: 2, BaseURL: "https://a.example"},
		{ProviderID: 1, BaseURL: "https://b.example"},
		{ProviderID: 1, BaseURL: "https://a.example", ProxyURL: "http://proxy.example:9000"},
	} {
		client := pool.acquire(route, 1)
		key := poolKey(route, 1)
		if previous, exists := clients[key]; exists && previous != client {
			t.Fatalf("相同 poolKey 返回了不同 Client: %s", key)
		}
		clients[key] = client
	}
	if len(clients) != 4 {
		t.Fatalf("应建成 4 个池, got=%d", len(clients))
	}
}

// TestUpstreamPoolRetiresOnRevisionChange：revision 递增时必须整体退役旧池，
// 否则上一版 snapshot 的代理 / Base URL / 凭据连接会被继续复用。
func TestUpstreamPoolRetiresOnRevisionChange(t *testing.T) {
	pool := newUpstreamPool()
	route := routing.Route{ProviderID: 1, BaseURL: "https://upstream.example"}

	before := pool.acquire(route, 1)
	after := pool.acquire(route, 2)
	if before == after {
		t.Fatal("revision 变更后沿用了旧 Client，旧连接无法退役")
	}
	if pool.revision != 2 {
		t.Fatalf("revision 未更新, got=%d", pool.revision)
	}
	if size := pool.size(); size != 1 {
		t.Fatalf("退役后池内只应剩新 revision 的 Client, got=%d", size)
	}
	// 回退到旧 revision 也必须重新建池，不能复用已经退役的 Client。
	back := pool.acquire(route, 1)
	if back == before {
		t.Fatal("旧 revision 的 Client 被复活")
	}
}

// TestUpstreamPoolReusesAcrossRequestsViaHandler：走完整 Handler 连续打两次 /v1/responses，
// 上游侧观察到底层连接是否被复用（RemoteAddr 相同 + 只握过一次手）。
func TestUpstreamPoolReusesAcrossRequestsViaHandler(t *testing.T) {
	var (
		mu         sync.Mutex
		handshakes int
	)
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("data: {\"type\":\"response.completed\"}\n\n"))
	}))
	upstream.Config.ConnState = func(conn net.Conn, state http.ConnState) {
		if state == http.StateNew {
			mu.Lock()
			handshakes++
			mu.Unlock()
		}
	}
	upstream.EnableHTTP2 = false
	upstream.Start()
	defer upstream.Close()

	handler := configuredHandler(t, upstream.URL, "secret-one", "model", "model")
	// 强制走连接池，不注入测试 Transport。
	handler.clientForRoute = nil

	for i := 0; i < 3; i++ {
		request := httptest.NewRequest(http.MethodPost, "/v1/responses", stringsReader(`{"model":"model","input":"hi"}`))
		request.Header.Set("X-TelePilot-Provider-ID", "1")
		request.Header.Set("X-TelePilot-Request-Scope", "inference")
		request.Header.Set("X-TelePilot-Request-ID", fmt.Sprintf("pool-%d", i))
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, request)
		if recorder.Code != http.StatusOK {
			t.Fatalf("第 %d 次请求 status=%d body=%s", i, recorder.Code, recorder.Body.String())
		}
	}

	mu.Lock()
	defer mu.Unlock()
	if handshakes != 1 {
		t.Fatalf("3 次连续请求应只建立 1 条上游连接, got=%d", handshakes)
	}
}

// TestUpstreamPoolCloseIdlesConnections：close() 之后池必须清空，socket 不应残留。
func TestUpstreamPoolCloseIdlesConnections(t *testing.T) {
	pool := newUpstreamPool()
	route := routing.Route{ProviderID: 1, BaseURL: "https://upstream.example"}
	if pool.acquire(route, 1) == nil {
		t.Fatal("acquire 返回 nil")
	}
	pool.close()
	if size := pool.size(); size != 0 {
		t.Fatalf("close 后池未清空, got=%d", size)
	}
	if pool.revision != 0 {
		t.Fatalf("close 后 revision 应归零, got=%d", pool.revision)
	}
}

// TestUpstreamClientHonoursIdleConnectionSettings：新 Client 必须真的允许连接复用
// （IdleConnTimeout > 0），否则"池化"只是缓存了对象，连接仍然一次一建。
func TestUpstreamClientHonoursIdleConnectionSettings(t *testing.T) {
	transport := newUpstreamClient(routing.Route{}).Transport.(*http.Transport)
	if transport.IdleConnTimeout <= 0 {
		t.Fatalf("IdleConnTimeout 必须大于 0 才能复用连接, got=%s", transport.IdleConnTimeout)
	}
	if transport.MaxIdleConnsPerHost < 2 {
		t.Fatalf("MaxIdleConnsPerHost 过小无法并发复用, got=%d", transport.MaxIdleConnsPerHost)
	}
	if transport.ResponseHeaderTimeout != upstreamResponseHeaderTimeout {
		t.Fatalf("ResponseHeaderTimeout 回归: got=%s", transport.ResponseHeaderTimeout)
	}
	if transport.Proxy != nil {
		t.Fatal("未选 Provider 代理时 Transport.Proxy 必须为 nil，不能继承进程环境变量")
	}
}

// TestUpstreamClientProxyOnlyFromRoute：route 指定代理时才走代理，且解析失败时直连
// （不因坏代理字符串把请求打到奇怪的地方）。
func TestUpstreamClientProxyOnlyFromRoute(t *testing.T) {
	selected := newUpstreamClient(routing.Route{ProxyURL: "http://selected-proxy.example:9000"}).Transport.(*http.Transport)
	if selected.Proxy == nil {
		t.Fatal("route 指定了代理但 Transport.Proxy 为 nil")
	}
	request, err := http.NewRequest(http.MethodGet, "https://upstream.example/v1/responses", nil)
	if err != nil {
		t.Fatal(err)
	}
	proxy, err := selected.Proxy(request)
	if err != nil {
		t.Fatal(err)
	}
	if proxy == nil || proxy.Host != "selected-proxy.example:9000" {
		t.Fatalf("代理未按 route 生效: %v", proxy)
	}

	broken := newUpstreamClient(routing.Route{ProxyURL: "://not a url"}).Transport.(*http.Transport)
	if broken.Proxy != nil {
		t.Fatal("无法解析的代理字符串应退化为直连")
	}
}

// TestHandlerCloseDrainsUpstreamPool：Handler.Close 必须把池交给进程退出逻辑。
func TestHandlerCloseDrainsUpstreamPool(t *testing.T) {
	store := control.NewStore()
	if err := store.Apply(contract.ConfigSnapshot{
		SchemaVersion:   1,
		ProtocolVersion: contract.ProtocolVersion,
		Revision:        1,
		Providers: []contract.ProviderConfig{{
			ID: 1, BaseURL: "https://upstream.example", APIKey: "key",
			Models: []string{"model"}, ModelsEndpoints: []string{"https://upstream.example/models"},
		}},
	}); err != nil {
		t.Fatal(err)
	}
	handler := NewHandler(store)
	handler.clientForRoute = nil
	handler.upstreamClient(routing.Route{ProviderID: 1, BaseURL: "https://upstream.example"}, 1)
	if handler.pool.size() == 0 {
		t.Fatal("池内应有 Client")
	}
	handler.Close()
	if handler.pool.size() != 0 {
		t.Fatalf("Close 后池未清空, got=%d", handler.pool.size())
	}
}

// TestUpstreamPoolConcurrentAcquireIsStable：并发 acquire 同一路由必须返回同一 Client，
// 且不能触发 map 并发写。
func TestUpstreamPoolConcurrentAcquireIsStable(t *testing.T) {
	pool := newUpstreamPool()
	route := routing.Route{ProviderID: 1, BaseURL: "https://upstream.example"}

	var wg sync.WaitGroup
	clients := make([]*http.Client, 32)
	for i := 0; i < len(clients); i++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			clients[index] = pool.acquire(route, 1)
		}(i)
	}
	wg.Wait()

	for i, client := range clients {
		if client != clients[0] {
			t.Fatalf("并发 acquire 返回了不同 Client: index=%d", i)
		}
	}
	if size := pool.size(); size != 1 {
		t.Fatalf("并发下池内应只有 1 个 Client, got=%d", size)
	}
}

func stringsReader(body string) io.Reader {
	return strings.NewReader(body)
}
