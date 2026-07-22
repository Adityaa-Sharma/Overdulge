import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import Login from '../src/routes/Login'

describe('Login placeholder route', () => {
  it('renders the login heading', () => {
    render(<Login />)
    expect(screen.getByRole('heading', { name: /log in/i })).toBeInTheDocument()
  })
})
