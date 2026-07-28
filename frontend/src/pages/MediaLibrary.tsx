/** 素材庫：所有上傳與生成的圖片／影片。 */
import { Card, Empty, Image, List, Tag, Typography } from 'antd';
import { MediaService } from '../services/generated';
import { unwrap } from '../lib/api';
import { useAsync } from '../lib/useAsync';
import { PageState } from '../components/PageState';

export function MediaLibrary() {
  const media = useAsync(() =>
    MediaService.listMedia({ page: 1, pageSize: 60 }).then((r) => unwrap<any>(r)),
  );

  return (
    <div>
      <Typography.Title level={3}>素材庫</Typography.Title>

      <PageState
        loading={media.loading}
        error={media.error}
        empty={!media.data?.items?.length}
        emptyText="尚無素材；生成任務完成後產物會出現在這裡"
        onRetry={media.reload}
      >
        <List
          grid={{ gutter: 12, xs: 2, sm: 3, md: 4, lg: 6 }}
          dataSource={media.data?.items ?? []}
          renderItem={(item: any) => (
            <List.Item>
              <Card
                size="small"
                cover={
                  item.file_type === 'image' ? (
                    <Image src={item.url} alt={item.filename} height={120} style={{ objectFit: 'cover' }} />
                  ) : item.file_type === 'video' ? (
                    <video src={item.url} controls style={{ width: '100%', height: 120, background: '#000' }} />
                  ) : (
                    <Empty image={null} description="檔案" style={{ padding: 24 }} />
                  )
                }
              >
                <Typography.Text ellipsis style={{ fontSize: 12 }}>
                  {item.filename || item.id}
                </Typography.Text>
                <div>
                  <Tag>{item.file_type}</Tag>
                </div>
              </Card>
            </List.Item>
          )}
        />
      </PageState>
    </div>
  );
}
