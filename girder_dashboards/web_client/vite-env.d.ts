/// <reference types="vite/client" />

declare module '*.pug' {
  const template: (locals?: Record<string, any>) => string;
  export default template;
}
