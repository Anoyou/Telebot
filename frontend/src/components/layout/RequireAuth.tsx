// 路由级守卫：调用一次 /api/auth/me，异常时兜底跳 /login
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { fetchMe } from "@/lib/auth";
import { Spinner } from "@/components/ui/misc";

export function RequireAuth() {
  const loc = useLocation();
  const { isLoading, isError } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: fetchMe,
    // 401 在 axios 拦截器里会触发跳转；这里仅根据 error 渲染兜底
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  if (isError) {
    return <Navigate to="/login" replace state={{ from: loc }} />;
  }

  return <Outlet />;
}
