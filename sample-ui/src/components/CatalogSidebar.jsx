import { useMemo, useState } from "react";
import { Search, ChevronDown, ChevronRight, BookOpen } from "lucide-react";
import catalog from "../data/prewrittenQueries.json";

/**
 * Left-side catalog of curated Cypher queries.
 *
 * - Grouped by `category` field
 * - Search box filters items by label (case-insensitive substring)
 * - Click an item → calls onPick(item) which sends both query + cypher
 *   to the backend via the SSE stream endpoint
 *
 * The cypher field is NEVER rendered to the user — only the natural-
 * language label appears as the user's message bubble in chat.
 */
export default function CatalogSidebar({ onPick, disabled }) {
  const [search, setSearch] = useState("");
  const [collapsedCategories, setCollapsedCategories] = useState(new Set());

  // Group items by category (preserving first-seen order)
  const grouped = useMemo(() => {
    const query = search.trim().toLowerCase();
    const out = [];
    const seen = new Map();

    for (const item of catalog) {
      if (query && !item.label.toLowerCase().includes(query)) continue;

      if (!seen.has(item.category)) {
        const bucket = { category: item.category, items: [] };
        seen.set(item.category, bucket);
        out.push(bucket);
      }
      seen.get(item.category).items.push(item);
    }
    return out;
  }, [search]);

  const toggleCategory = (name) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const handlePick = (item) => {
    if (disabled) return;
    onPick?.(item);
  };

  return (
    <aside className="catalog-sidebar">
      <div className="catalog-header">
        <BookOpen size={18} />
        <span className="catalog-title">Query Catalog</span>
      </div>

      <div className="catalog-search">
        <Search size={14} className="catalog-search-icon" />
        <input
          type="text"
          placeholder="Search catalog..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="catalog-search-input"
        />
      </div>

      <div className="catalog-items">
        {grouped.length === 0 && (
          <div className="catalog-empty">No queries match</div>
        )}

        {grouped.map(({ category, items }) => {
          const collapsed = collapsedCategories.has(category);
          return (
            <div key={category} className="catalog-category">
              <button
                type="button"
                className="catalog-category-header"
                onClick={() => toggleCategory(category)}
                aria-expanded={!collapsed}
              >
                {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                <span>{category}</span>
                <span className="catalog-category-count">{items.length}</span>
              </button>

              {!collapsed && (
                <ul className="catalog-category-items">
                  {items.map((item) => (
                    <li key={item.id}>
                      <button
                        type="button"
                        className="catalog-item-button"
                        onClick={() => handlePick(item)}
                        disabled={disabled}
                        title={item.label}
                      >
                        {item.label}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      <div className="catalog-footer">
        <span>{catalog.length} curated queries</span>
      </div>
    </aside>
  );
}
