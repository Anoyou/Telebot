/** 模型家族单色图标（currentColor），数据与 iOS 共用：modelBrandIcons.json（来源 lobe-icons / simple-icons）。 */
import type { ModelBrandId } from "@/lib/modelBrand";
import iconData from "./modelBrandIcons.json" with { type: "json" };

export type BrandIconDef = { viewBox: string; paths: string; source: string; fillRule: "evenodd" | "nonzero" };

export const MODEL_BRAND_ICONS = iconData.icons as Partial<Record<ModelBrandId, BrandIconDef>>;
