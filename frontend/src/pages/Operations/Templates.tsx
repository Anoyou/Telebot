import { PageShell } from "@/components/layout/PageScaffold";
import { CommandTemplates } from "./TemplatesEditor";
import { OperationsWorkspaceHeader } from "./WorkspaceHeader";

export function OperationsTemplatesPage() {
  return (
    <PageShell>
      <OperationsWorkspaceHeader activeTab="templates" />
      <CommandTemplates />
    </PageShell>
  );
}
