import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  createRule,
  deleteRule,
  dryRunRule,
  listRules,
  updateRule,
} from "@/api/features";
import { listAccountFeatures, toggleAccountFeature } from "@/api/accounts";
import { getErrMsg } from "@/lib/api";
import type {
  RuleCreate,
  RuleDryRunRequest,
  RuleDryRunResponse,
  RuleOut,
  RuleUpdate,
} from "@/api/types";

export interface UseRuleCrudOptions {
  aid: number;
  ruleKind: string;
  /** 若提供，则同时管理该 feature 的总开关（toggleAccountFeature） */
  featureKey?: string;
}

export interface RuleCrudApi {
  // queries
  rulesQ: ReturnType<typeof useQuery<RuleOut[]>>;
  isFeatureEnabled: boolean;
  // mutations
  toggleFeature: (next: boolean) => void;
  saveRule: (args: {
    editing: RuleOut | null;
    payload: RuleCreate | RuleUpdate;
    onSuccess?: () => void;
  }) => Promise<void>;
  saving: boolean;
  removeRule: (rid: number) => void;
  removing: boolean;
  dryRun: (args: {
    rid: number;
    payload: RuleDryRunRequest;
    onSuccess?: (res: RuleDryRunResponse) => void;
  }) => void;
  dryRunPending: boolean;
}

/**
 * Rule CRUD 的共享 hook。封装：
 *   - features 列表查询（用于判断功能总开关）
 *   - rules 列表查询
 *   - save / delete / toggle 三个 mutation + 失败 toast + 成功 invalidate
 *   - dry-run mutation
 *
 * 不负责 UI 层（表单 state、Dialog 开关）——那些每个 feature 不同。
 */
export function useRuleCrud(opts: UseRuleCrudOptions): RuleCrudApi {
  const { aid, ruleKind, featureKey } = opts;
  const qc = useQueryClient();

  const featuresQ = useQuery({
    queryKey: ["account", aid, "features"],
    queryFn: () => listAccountFeatures(aid),
    enabled: !!aid && !!featureKey,
  });
  const featureItem = featureKey
    ? featuresQ.data?.find((x) => x.feature_key === featureKey)
    : undefined;
  const isFeatureEnabled = !!featureItem?.enabled;

  const rulesQ = useQuery({
    queryKey: ["account", aid, "rules", ruleKind],
    queryFn: () => listRules(aid, ruleKind),
    enabled: !!aid,
  });

  const featureToggleMut = useMutation({
    mutationFn: (next: boolean) => {
      if (!featureKey) throw new Error("featureKey 未配置");
      return toggleAccountFeature(aid, featureKey, next);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account", aid, "features"] });
      qc.invalidateQueries({ queryKey: ["matrix"] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const saveMut = useMutation({
    mutationFn: async (args: {
      editing: RuleOut | null;
      payload: RuleCreate | RuleUpdate;
    }) => {
      if (!args.editing) {
        await createRule(aid, ruleKind, args.payload as RuleCreate);
      } else {
        await updateRule(aid, ruleKind, args.editing.id, args.payload);
      }
    },
    onSuccess: () => {
      toast.success("已保存");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", ruleKind] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const delMut = useMutation({
    mutationFn: (rid: number) => deleteRule(aid, ruleKind, rid),
    onSuccess: () => {
      toast.success("已删除");
      qc.invalidateQueries({ queryKey: ["account", aid, "rules", ruleKind] });
    },
    onError: (err) => toast.error(getErrMsg(err)),
  });

  const dryMut = useMutation({
    mutationFn: (args: { rid: number; payload: RuleDryRunRequest }) =>
      dryRunRule(aid, ruleKind, args.rid, args.payload),
    onError: (err) => toast.error(getErrMsg(err)),
  });

  return {
    rulesQ,
    isFeatureEnabled,
    toggleFeature: (next) => featureToggleMut.mutate(next),
    saveRule: async ({ editing, payload, onSuccess }) => {
      await saveMut.mutateAsync(
        { editing, payload },
        { onSuccess: () => onSuccess?.() },
      );
    },
    saving: saveMut.isPending,
    removeRule: (rid) => delMut.mutate(rid),
    removing: delMut.isPending,
    dryRun: ({ rid, payload, onSuccess }) =>
      dryMut.mutate(
        { rid, payload },
        { onSuccess: (res) => onSuccess?.(res) },
      ),
    dryRunPending: dryMut.isPending,
  };
}
