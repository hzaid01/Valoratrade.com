/*
  # Create User API Keys Table

  1. New Tables
    - `user_api_keys`
      - `id` (uuid, primary key) - Unique identifier
      - `user_id` (uuid, foreign key) - References auth.users
      - `binance_api_key` (text) - Encrypted Binance API key
      - `binance_secret_key` (text) - Encrypted Binance secret key
      - `openai_api_key` (text) - Encrypted OpenAI API key
      - `created_at` (timestamptz) - When the record was created
      - `updated_at` (timestamptz) - When the record was last updated

  2. Security
    - Enable RLS on `user_api_keys` table
    - Add policy for authenticated users to read their own API keys
    - Add policy for authenticated users to insert their own API keys
    - Add policy for authenticated users to update their own API keys
    - Add policy for authenticated users to delete their own API keys

  3. Important Notes
    - API keys are stored encrypted for security
    - Each user can only access their own API keys
    - Foreign key constraint ensures data integrity with auth.users
*/

CREATE TABLE IF NOT EXISTS user_api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL UNIQUE,
  binance_api_key text DEFAULT '',
  binance_secret_key text DEFAULT '',
  openai_api_key text DEFAULT '',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

ALTER TABLE user_api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own API keys"
  ON user_api_keys FOR SELECT
  TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own API keys"
  ON user_api_keys FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own API keys"
  ON user_api_keys FOR UPDATE
  TO authenticated
  USING (auth.uid() = user_id)
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete own API keys"
  ON user_api_keys FOR DELETE
  TO authenticated
  USING (auth.uid() = user_id);