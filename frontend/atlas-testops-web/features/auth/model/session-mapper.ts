import type { PlatformSessionDto, SessionViewModel } from "./session";

function initials(displayName: string, email: string): string {
  const compactName = displayName.trim();
  if (compactName) {
    const glyphs = Array.from(compactName.replace(/\s+/g, ""));
    return glyphs.slice(0, 2).join("").toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}

export function mapSessionDto(dto: PlatformSessionDto): SessionViewModel {
  return {
    user: {
      id: dto.user.id,
      displayName: dto.user.displayName,
      email: dto.user.email,
      initials: initials(dto.user.displayName, dto.user.email)
    },
    workspace: {
      tenantId: dto.tenant.id,
      tenantName: dto.tenant.name,
      projectId: dto.project.id,
      projectName: dto.project.name
    },
    roles: [...dto.roles],
    authenticationMethod: dto.authenticationMethod,
    expiresAt: new Date(dto.expiresAt)
  };
}
