import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
import { listModelBrands, resolveModelBrand, type ModelBrandId } from "@/lib/modelBrand";
import { MODEL_BRAND_ICONS } from "@/lib/modelBrandIcons";

const BRAND_META = Object.fromEntries(listModelBrands().map((brand) => [brand.id, brand])) as unknown as Record<
  ModelBrandId,
  { label: string; accent: string }
>;

function AutoMark({ className, title, color }: { className?: string; title?: string; color?: string }) {
  const style: CSSProperties | undefined = color ? { color } : undefined;
  return (
    <svg viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined} className={cn("shrink-0", className)} style={style}>
      {title ? <title>{title}</title> : null}
      <path
        fill="currentColor"
        d="M12 3.2 13.4 8.4 18.8 9.8 13.4 11.2 12 16.4 10.6 11.2 5.2 9.8 10.6 8.4 12 3.2Zm5.8 9.6.8 2.6 2.6.8-2.6.8-.8 2.6-.8-2.6-2.6-.8 2.6-.8.8-2.6Z"
      />
    </svg>
  );
}

function UnknownMark({ className, title, color }: { className?: string; title?: string; color?: string }) {
  const style: CSSProperties | undefined = color ? { color } : undefined;
  return (
    <svg viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined} className={cn("shrink-0", className)} style={style}>
      {title ? <title>{title}</title> : null}
      <path
        fill="currentColor"
        d="M12 3.4a8.6 8.6 0 1 1 0 17.2 8.6 8.6 0 0 1 0-17.2Zm0 2.2a6.4 6.4 0 1 0 0 12.8 6.4 6.4 0 0 0 0-12.8Zm0 2.3c.8 0 1.4.6 1.4 1.4v2.3h2.3a1.4 1.4 0 0 1 0 2.8h-2.3v2.3a1.4 1.4 0 0 1-2.8 0v-2.3H8.3a1.4 1.4 0 0 1 0-2.8h2.3V9.3c0-.8.6-1.4 1.4-1.4Z"
      />
    </svg>
  );
}

function SourcedBrandMark({
  brandId,
  className,
  title,
  color,
}: {
  brandId: ModelBrandId;
  className?: string;
  title?: string;
  color?: string;
}) {
  if (brandId === "auto") return <AutoMark className={className} title={title} color={color} />;
  const icon = MODEL_BRAND_ICONS[brandId];
  if (!icon) return <UnknownMark className={className} title={title} color={color} />;

  const style: CSSProperties | undefined = color ? { color } : undefined;
  return (
    <svg
      viewBox={icon.viewBox}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      className={cn("shrink-0", className)}
      style={style}
      data-icon-source={icon.source}
      fillRule={icon.fillRule}
      // Real brand paths from lobe-icons / simple-icons, monochrome currentColor.
      dangerouslySetInnerHTML={{ __html: (title ? `<title>${escapeHtml(title)}</title>` : "") + icon.paths }}
    />
  );
}

export function ModelBrandLogo({
  model,
  providerName,
  auto = false,
  brandId,
  className,
  size = 14,
  tinted = true,
  title,
}: {
  model?: string | null;
  providerName?: string | null;
  auto?: boolean;
  brandId?: ModelBrandId;
  className?: string;
  size?: number;
  /** true：品牌强调色；false：跟随当前文字色，适配主题 */
  tinted?: boolean;
  title?: string;
}) {
  const resolved = resolveModelBrand(model, providerName, { auto: auto || brandId === "auto" });
  const id = brandId || resolved.id;
  const meta = BRAND_META[id] || BRAND_META.unknown;
  const label = id === resolved.id ? resolved.label : meta.label;
  const accent = BRAND_META[id]?.accent || resolved.accent || meta.accent;

  return (
    <span
      className={cn("inline-flex items-center justify-center leading-none", className)}
      style={{ width: size, height: size }}
      data-model-brand={id}
    >
      <SourcedBrandMark
        brandId={id}
        className="h-full w-full"
        title={title || label}
        color={tinted ? accent : undefined}
      />
    </span>
  );
}
