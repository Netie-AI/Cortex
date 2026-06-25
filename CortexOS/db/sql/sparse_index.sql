-- Sparse FTS index for listings retrieval (AI1.2)
-- Uses dictionary 'simple' to preserve exact Malaysian proper nouns/project names.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'listings'
  ) THEN
    ALTER TABLE public.listings
      ADD COLUMN IF NOT EXISTS fts_doc tsvector;

    CREATE INDEX IF NOT EXISTS listings_fts_gin
      ON public.listings USING GIN (fts_doc);

    UPDATE public.listings
    SET fts_doc = to_tsvector(
      'simple',
      coalesce(title, '') || ' ' ||
      coalesce(description, '') || ' ' ||
      coalesce(project_name, '') || ' ' ||
      coalesce(address, '') || ' ' ||
      coalesce(postcode, '')
    );
  END IF;
END $$;
