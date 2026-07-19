import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Next.js 16 renamed the `middleware` file convention to `proxy` — see
// node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md.
export function proxy(request: NextRequest) {
  const token = request.cookies.get("katha_token");
  const isAuthRoute =
    request.nextUrl.pathname.startsWith("/family/login") ||
    request.nextUrl.pathname.startsWith("/family/auth");

  if (!token && !isAuthRoute) {
    return NextResponse.redirect(new URL("/family/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/family/:path*"],
};
