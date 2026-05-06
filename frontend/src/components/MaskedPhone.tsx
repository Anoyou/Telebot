// 手机号码遮掩 + 点击切换显示。
//
// 遮掩规则：保留 + 号和国家代码（地区码），其后所有数字位换成 *。
//   "+8613812345678"  →  "+86***********"
//   "+15555551234"    →  "+1**********"
//   "+447911123456"   →  "+44**********"
//   "+8521234567"     →  "+852*******"
//
// 国家代码长度不固定（1 / 2 / 3 位），用 ITU 公开的 E.164 表中常见的 1 位
// （+1 NANP，+7 俄/哈）和 3 位前缀做白名单匹配，其余一律按 2 位处理。
import { useMemo, useState } from "react";
import { Eye, EyeOff, Phone } from "lucide-react";

import { cn } from "@/lib/utils";

// ── 遮掩逻辑 ───────────────────────────────────────────────────────

const COUNTRY_CODES_1 = new Set(["1", "7"]);

// E.164 中明确的 3 位国家代码集合（覆盖绝大多数；不在表里的按 2 位处理）
const COUNTRY_CODES_3 = new Set([
  // 非洲
  "212","213","216","218","220","221","222","223","224","225","226","227","228",
  "229","230","231","232","233","234","235","236","237","238","239","240","241",
  "242","243","244","245","246","247","248","249","250","251","252","253","254",
  "255","256","257","258","260","261","262","263","264","265","266","267","268",
  "269","290","291","297","298","299",
  // 欧洲
  "350","351","352","353","354","355","356","357","358","359","370","371","372",
  "373","374","375","376","377","378","380","381","382","383","385","386","387",
  "389","420","421","423",
  // 美洲（非 +1 NANP 部分）
  "500","501","502","503","504","505","506","507","508","509","590","591","592",
  "593","594","595","596","597","598","599",
  // 亚洲 / 中东 / 太平洋
  "670","672","673","674","675","676","677","678","679","680","681","682","683",
  "685","686","687","688","689","690","691","692",
  "850","852","853","855","856","880","886",
  "960","961","962","963","964","965","966","967","968","970","971","972","973",
  "974","975","976","977",
  "992","993","994","995","996","998",
]);

function getCountryCodeLength(digits: string): number {
  if (digits.length === 0) return 0;
  if (COUNTRY_CODES_1.has(digits[0])) return 1;
  if (digits.length >= 3 && COUNTRY_CODES_3.has(digits.slice(0, 3))) return 3;
  return Math.min(2, digits.length);
}

export function maskPhone(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";

  const hasPlus = trimmed.startsWith("+");
  const digits = trimmed.replace(/\D/g, "");
  if (digits.length === 0) return "*".repeat(trimmed.length);

  const ccLen = getCountryCodeLength(digits);
  const cc = digits.slice(0, ccLen);
  const tail = digits.length - ccLen;
  return (hasPlus ? "+" : "") + cc + "*".repeat(tail);
}

// ── 组件 ───────────────────────────────────────────────────────────

interface MaskedPhoneProps {
  phone: string;
  className?: string;
  /** 自定义图标尺寸 class，默认 h-3.5 w-3.5（卡片用）。详情页可传更大 */
  iconClassName?: string;
}

export function MaskedPhone({ phone, className, iconClassName }: MaskedPhoneProps) {
  const [shown, setShown] = useState(false);
  const masked = useMemo(() => maskPhone(phone), [phone]);

  return (
    <button
      type="button"
      onClick={() => setShown((s) => !s)}
      className={cn(
        "group inline-flex max-w-full items-center gap-1.5 rounded-sm",
        "text-left text-muted-foreground transition-colors hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      aria-label={shown ? "点击隐藏手机号" : "点击显示完整手机号"}
    >
      <Phone className={cn("shrink-0", iconClassName ?? "h-3.5 w-3.5")} />
      <span className="truncate font-mono">{shown ? phone : masked}</span>
      {shown ? (
        <EyeOff className="h-3 w-3 shrink-0 opacity-50 transition-opacity group-hover:opacity-100" />
      ) : (
        <Eye className="h-3 w-3 shrink-0 opacity-50 transition-opacity group-hover:opacity-100" />
      )}
    </button>
  );
}
