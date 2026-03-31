import { createClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error('Missing Supabase environment variables. Check your .env file.')
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: true,
  },
})

// Types matching the backend schema
export interface Scan {
  id: string
  user_id: string | null
  url: string
  profession: 'realestate' | 'lending'
  status: 'pending' | 'running' | 'complete' | 'error'
  score: number | null
  results: ScanResult[] | null
  created_at: string
  completed_at: string | null
}

export interface ScanResult {
  id: string
  label: string
  category: string
  status: 'pass' | 'warn' | 'fail' | 'na'
  detail: string
  remediation?: string
  screenshot_url?: string
}

export interface UserProfile {
  id: string
  email: string
  tier: 'free' | 'single' | 'pro' | 'broker'
  scans_used: number
  created_at: string
}
