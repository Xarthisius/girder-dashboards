/// <reference types="vite/client" />

declare module '*.pug' {
  const template: (locals?: Record<string, any>) => string;
  export default template;
}

// Girder core is not a build-time dependency of this bundle: it is the runtime
// `girder` global the app injects before plugin scripts load. In-tree plugins
// type it as `Girder` imported from `@girder/core`; an out-of-tree plugin has
// no such package to import from, so the global is declared untyped here.
declare const girder: any;
