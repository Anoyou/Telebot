package version

import "testing"

func TestResolvedBuildCommitPrefersLinkerValue(t *testing.T) {
	old := BuildCommit
	t.Cleanup(func() { BuildCommit = old })
	t.Setenv(BuildCommitEnv, "from-env")
	BuildCommit = "abc123"
	if got := ResolvedBuildCommit(); got != "abc123" {
		t.Fatalf("got %q", got)
	}
}

func TestResolvedBuildCommitFallsBackToEnv(t *testing.T) {
	old := BuildCommit
	t.Cleanup(func() { BuildCommit = old })
	BuildCommit = "dev"
	t.Setenv(BuildCommitEnv, " def456 ")
	if got := Info().BuildCommit; got != "def456" {
		t.Fatalf("got %q", got)
	}
}

func TestResolvedBuildCommitDefaultsToDev(t *testing.T) {
	old := BuildCommit
	t.Cleanup(func() { BuildCommit = old })
	BuildCommit = ""
	t.Setenv(BuildCommitEnv, "")
	if got := ResolvedBuildCommit(); got != "dev" {
		t.Fatalf("got %q", got)
	}
}
