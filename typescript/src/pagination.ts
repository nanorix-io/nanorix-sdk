/**
 * Stripe-pattern cursor pagination — async iterator for resource listings.
 *
 * Used by `client.usage.listItems()` (Gap 13). The API surface is
 * `GET /v1/usage/items?starting_after=<id>&limit=N` returning
 * `{object: "list", data: [...], has_more: bool}`. SDK consumers iterate
 * via `for await (const item of client.usage.listItems()) { ... }` without
 * paging manually.
 *
 * TypeScript-specific: only async; JS has no sync HTTP.
 */

export interface PageResponse<TRaw> {
  object: string;
  data: TRaw[];
  has_more: boolean;
}

export interface FetchPageFn<TRaw> {
  (cursor: string | undefined, limit: number): Promise<PageResponse<TRaw>>;
}

export interface ParseItemFn<TRaw, T> {
  (raw: TRaw): T;
}

/**
 * AsyncIterable cursor-paginated resource listing.
 *
 * Auto-pages via fetch-N+1 + cursor advance (last.id → starting_after).
 * Stops when server signals `has_more: false`.
 */
export class Page<TRaw extends { id: string }, T> implements AsyncIterable<T> {
  private buffer: T[] = [];
  private cursor: string | undefined;
  private hasMore = true;
  private firstFetch = true;

  constructor(
    private readonly fetchPage: FetchPageFn<TRaw>,
    private readonly parseItem: ParseItemFn<TRaw, T>,
    private readonly limit: number,
    initialCursor?: string,
  ) {
    this.cursor = initialCursor;
  }

  [Symbol.asyncIterator](): AsyncIterator<T> {
    return this;
  }

  async next(): Promise<IteratorResult<T>> {
    if (this.buffer.length === 0 && (this.hasMore || this.firstFetch)) {
      await this.fetchNext();
    }
    if (this.buffer.length === 0) {
      return { value: undefined, done: true };
    }
    return { value: this.buffer.shift()!, done: false };
  }

  private async fetchNext(): Promise<void> {
    const page = await this.fetchPage(this.cursor, this.limit);
    this.buffer = page.data.map((d) => this.parseItem(d));
    this.hasMore = Boolean(page.has_more);
    if (page.data.length > 0) {
      this.cursor = page.data[page.data.length - 1].id;
    }
    this.firstFetch = false;
  }

  /** Materialize all items into an array (consumes the iterator). */
  async toArray(): Promise<T[]> {
    const out: T[] = [];
    for await (const item of this) {
      out.push(item);
    }
    return out;
  }
}
