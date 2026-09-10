import { Link } from 'react-router-dom';
import { Compass } from 'lucide-react';
import { Button, Card, EmptyState } from '../components/ui';

export function NotFoundPage() {
  return (
    <Card>
      <EmptyState
        icon={Compass}
        title="That page does not exist"
        action={
          <Link to="/">
            <Button variant="primary">Go to the dashboard</Button>
          </Link>
        }
      >
        Check the address, or head back to the dashboard.
      </EmptyState>
    </Card>
  );
}
