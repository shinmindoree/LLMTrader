export type CryptoPanicPostDto = {
  id: string;
  title: string;
  url: string;
  publishedAt: string | null;
  sourceTitle: string | null;
  domain: string | null;
  currencies: string[];
};
