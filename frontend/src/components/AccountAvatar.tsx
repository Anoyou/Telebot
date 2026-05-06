// 圆形账号头像：尝试加载后端 /avatar 接口；
// - 失败（404 / worker 离线 / 账号无头像）→ 渲染首字母 + 基于 ID 的稳定背景色
// - 加载成功 → 渲染图片（object-cover 保证圆形不变形）
import { useState, useMemo } from "react";

import { avatarUrl } from "@/api/accounts";
import { cn } from "@/lib/utils";

interface AccountAvatarProps {
  /** 账号 ID（系统 PK，不是 TG user id） */
  id: number;
  /** 显示名 / @用户名，用于决定首字母 fallback；都为空时回落到 # */
  name?: string | null;
  /** 用户名，作为 name 的次选 */
  username?: string | null;
  /** 像素尺寸，默认 32（h-8 w-8） */
  size?: number;
  className?: string;
}

// 8 个柔和背景色，按 id 取模分配；保证同一账号始终拿到同一颜色
const PALETTE = [
  "bg-rose-200 text-rose-800",
  "bg-orange-200 text-orange-800",
  "bg-amber-200 text-amber-800",
  "bg-emerald-200 text-emerald-800",
  "bg-sky-200 text-sky-800",
  "bg-indigo-200 text-indigo-800",
  "bg-fuchsia-200 text-fuchsia-800",
  "bg-slate-200 text-slate-800",
];

export function AccountAvatar({
  id,
  name,
  username,
  size = 32,
  className,
}: AccountAvatarProps) {
  // 头像是否加载失败：失败一次后切到首字母，不再无脑重试
  const [failed, setFailed] = useState(false);

  // 取首字母：display_name 优先，其次 @username，最后回落 "#"
  const initial = useMemo(() => {
    const src = (name && name.trim()) || (username && username.trim()) || "";
    if (!src) return "#";
    // 取第一个可显示字符（兼容中文 / emoji surrogate pair）
    const codePoint = src.codePointAt(0);
    return codePoint ? String.fromCodePoint(codePoint).toUpperCase() : "#";
  }, [name, username]);

  const colorClass = PALETTE[id % PALETTE.length];

  const style = { width: size, height: size, fontSize: Math.round(size * 0.42) };

  if (failed) {
    return (
      <div
        className={cn(
          "shrink-0 inline-flex items-center justify-center rounded-full font-medium select-none",
          colorClass,
          className,
        )}
        style={style}
        aria-label={name || username || `账号 ${id}`}
      >
        {initial}
      </div>
    );
  }

  return (
    <img
      src={avatarUrl(id)}
      alt={name || username || `账号 ${id}`}
      width={size}
      height={size}
      style={style}
      onError={() => setFailed(true)}
      // referrerPolicy 与 crossOrigin 用默认值即可：同源 + cookie 已自动带上
      className={cn(
        "shrink-0 inline-block rounded-full object-cover bg-muted",
        className,
      )}
    />
  );
}
