import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ExternalLink } from "lucide-react";
import CodeBlock from "./CodeBlock";

const remarkPlugins = [remarkGfm];

const components = {
  code({ inline, className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || "");
    const lang = match ? match[1] : null;
    const text = String(children).replace(/\n$/, "");

    if (!inline && (lang || text.includes("\n"))) {
      return <CodeBlock language={lang}>{text}</CodeBlock>;
    }

    return (
      <code className="inline-code" {...props}>
        {children}
      </code>
    );
  },

  table({ children }) {
    return (
      <div className="table-wrapper">
        <table>{children}</table>
      </div>
    );
  },

  a({ href, children, ...props }) {
    const isExternal =
      href && (href.startsWith("http://") || href.startsWith("https://"));

    // Shorten auto-linked URLs: if the link text is the raw URL itself,
    // display a readable label (hostname + first path segment) instead
    let displayChildren = children;
    const childText = String(children);
    if (isExternal && childText === href) {
      try {
        const u = new URL(href);
        const host = u.hostname.replace(/^www\./, "");
        const firstPath = u.pathname.split("/").find(Boolean);
        displayChildren = firstPath ? `${host}/${firstPath}` : host;
      } catch {
        displayChildren = children;
      }
    }

    return (
      <a
        href={href}
        target={isExternal ? "_blank" : undefined}
        rel={isExternal ? "noopener noreferrer" : undefined}
        title={isExternal ? href : undefined}
        {...props}
      >
        {displayChildren}
        {isExternal && (
          <ExternalLink
            size={12}
            style={{ marginLeft: 3, verticalAlign: "middle" }}
          />
        )}
      </a>
    );
  },

  img({ src, alt, ...props }) {
    return (
      <img
        src={src}
        alt={alt || ""}
        loading="lazy"
        style={{ borderRadius: "var(--radius-md)", margin: "0.5em 0" }}
        {...props}
      />
    );
  },
};

export default function MarkdownRenderer({ content }) {
  return (
    <div className="prose">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
