declare module "aircall-everywhere" {
  type AircallWorkspaceOptions = {
    domToLoadWorkspace: string;
    size?: "small" | "big" | "auto";
    debug?: boolean;
    onLogin?: (settings?: unknown) => void;
    onLogout?: () => void;
  };

  export default class AircallWorkspace {
    constructor(options?: AircallWorkspaceOptions);
    on(event: string, callback: (payload?: unknown) => void): void;
    removeListener(event: string): boolean;
  }
}
