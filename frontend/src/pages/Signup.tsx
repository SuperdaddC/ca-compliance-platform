import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../App'
import Navbar from '../components/Navbar'
import { supabase } from '../lib/supabase'

export default function Signup() {
  const { user: _user } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [confirmationSent, setConfirmationSent] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)

    const { data, error: err } = await supabase.auth.signUp({ email, password })
    if (err) {
      setError(err.message)
      setLoading(false)
    } else if (data.session) {
      // Email confirmation disabled — user is logged in immediately
      navigate('/dashboard')
    } else {
      // Email confirmation required — show the check-your-email message
      setConfirmationSent(true)
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Navbar />
      <div className="max-w-md mx-auto px-4 py-16">
        <h1 className="text-2xl font-bold text-gray-900 mb-6 text-center">Create an account</h1>
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-8">

          {confirmationSent ? (
            <div className="text-center py-4">
              <div className="text-4xl mb-4">📧</div>
              <h2 className="text-lg font-bold text-gray-900 mb-2">Check your email</h2>
              <p className="text-sm text-gray-600 mb-4">
                We sent a confirmation link to <strong>{email}</strong>. Click the link to activate your account, then come back and log in.
              </p>
              <p className="text-xs text-gray-400 mb-6">Don't see it? Check your spam folder.</p>
              <Link
                to="/login"
                className="inline-block bg-brand-gold hover:bg-brand-gold-dark text-white font-bold py-3 px-8 rounded-xl transition-colors"
              >
                Go to Log in
              </Link>
            </div>
          ) : (
            <>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-brand-gold focus:border-transparent"
                    required
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">Password</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full px-4 py-3 rounded-lg border border-gray-300 focus:outline-none focus:ring-2 focus:ring-brand-gold focus:border-transparent"
                    required
                    minLength={6}
                  />
                </div>
                {error && <p className="text-sm text-red-600">{error}</p>}
                <button
                  type="submit"
                  disabled={loading}
                  className="w-full bg-brand-gold hover:bg-brand-gold-dark text-white font-bold py-3 rounded-xl transition-colors disabled:opacity-50"
                >
                  {loading ? 'Creating account…' : 'Sign up'}
                </button>
              </form>
              <p className="text-center text-sm text-gray-500 mt-4">
                Already have an account?{' '}
                <Link to="/login" className="text-brand-blue font-medium hover:text-brand-gold">Log in</Link>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
