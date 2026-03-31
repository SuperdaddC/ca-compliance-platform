-- Migration: Update profession values to match v2 scanner
-- Run this BEFORE deploying v2 backend/frontend
-- Safe to run multiple times (idempotent)

BEGIN;

-- Update existing scan records
UPDATE public.scans
  SET profession = 'realestate'
  WHERE profession = 'real_estate';

UPDATE public.scans
  SET profession = 'lending'
  WHERE profession = 'mortgage';

-- Update the check constraint to accept new values
ALTER TABLE public.scans
  DROP CONSTRAINT IF EXISTS scans_profession_check;

ALTER TABLE public.scans
  ADD CONSTRAINT scans_profession_check
  CHECK (profession IN ('realestate', 'lending'));

COMMIT;
