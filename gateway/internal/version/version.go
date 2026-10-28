package version

import (
	"os"
	"strings"

	"github.com/anoyou/telepilot/gateway/contract"
)

const (
	Release                 = "0.1.0-beta.3"
	UpstreamCommit          = "ffdb9c9fbc78a6235d59c9ccbdc4243ba35ecdcd"
	CodexContractReviewDate = "2026-08-04"
)

// BuildCommit 可在编译时用 -ldflags "-X …/version.BuildCommit=<sha>" 注入。
var BuildCommit = "dev"

// BuildCommitEnv 是运行时兜底来源：web 镜像在最后一层用 ENV 写入构建提交，
// 让 Gateway 的编译层不随每次提交失效（多架构镜像可直接复用缓存）。
const BuildCommitEnv = "TELEPILOT_GATEWAY_BUILD_COMMIT"

// ResolvedBuildCommit 优先使用编译期注入值；未注入（"dev" 或空）时读取运行时环境变量。
func ResolvedBuildCommit() string {
	commit := strings.TrimSpace(BuildCommit)
	if commit != "" && commit != "dev" {
		return commit
	}
	if env := strings.TrimSpace(os.Getenv(BuildCommitEnv)); env != "" {
		return env
	}
	if commit == "" {
		return "dev"
	}
	return commit
}

func Info() contract.VersionInfo {
	return contract.VersionInfo{
		Version:                 Release,
		ProtocolVersion:         contract.ProtocolVersion,
		UpstreamCommit:          UpstreamCommit,
		BuildCommit:             ResolvedBuildCommit(),
		CodexContractReviewDate: CodexContractReviewDate,
	}
}
