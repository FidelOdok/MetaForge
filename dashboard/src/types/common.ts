/** Paginated API response wrapper */
export interface PaginatedResponse<T> {
  data: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

/** Standardized API error shape */
export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  timestamp: string;
}

/** Sort direction for table columns */
export type SortDirection = 'asc' | 'desc';

/** Column sort configuration */
export interface SortConfig {
  field: string;
  direction: SortDirection;
}

/** Generic metadata record */
export type Metadata = Record<string, string | number | boolean>;
